"""
Template Component main class.

"""
import logging
import backoff
import asyncio
import datetime

import requests
from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from configuration import Configuration
from selectolax.parser import HTMLParser
from keboola.utils import parse_datetime_interval, split_dates_to_chunks
from keboola.csvwriter import ElasticDictWriter
from playwright.async_api import async_playwright


class TableNotFoundException(Exception):
    pass


class Component(ComponentBase):

    def __init__(self):
        super().__init__()
        self.session = requests.Session()

    def _init_configuration(self) -> None:
        self.validate_configuration_parameters(Configuration.get_dataclass_required_parameters())
        self.cfg: Configuration = Configuration.load_from_dict(self.configuration.parameters)

    async def _login(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            await page.set_default_timeout(10000)
            await page.goto(f'https://heureka.{self.cfg.country}')
            try:
                await page.click('#didomi-notice-agree-button')
            except Exception:
                logging.info("No cookies popup")

            if self.cfg.country == "cz":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.get_by_text('Administrace e-shopu').click()
                await page.wait_for_selector('button:has-text("Přihlásit se e-mailem")', timeout=20_000)
                await page.fill('#login-email', self.cfg.credentials.email)
                await page.fill('#login-password', self.cfg.credentials.pswd_password)
                await page.click('button:has-text("Přihlásit se e-mailem")', timeout=20_000)
            elif self.cfg.country == "sk":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.get_by_text('Administrácia e-shopu').click()
                await page.wait_for_selector('button:has-text("Prihlásiť sa e-mailom")', timeout=20_000)
                await page.fill('#login-email', self.cfg.credentials.email)
                await page.fill('#login-password', self.cfg.credentials.pswd_password)
                await page.click('button:has-text("Prihlásiť sa e-mailom")', timeout=20_000)
            else:
                raise UserException("Country not supported")

            cookies = await context.cookies()
            for cookie in cookies:
                self.session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
            await browser.close()

    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    def login(self):
        asyncio.run(self._login())

    def run(self):
        """
        Main execution code
        """

        self._init_configuration()

        eshop_id = self.cfg.report_settings.eshop_id
        date_from, date_to = parse_datetime_interval(self.cfg.report_settings.date_from,
                                                     self.cfg.report_settings.date_to)

        if (datetime.datetime.now() - date_from).days > 365:
            print("Cannot get data older than 1 year, downloading data for the last 365 days.")
            date_from = datetime.datetime.now() - datetime.timedelta(days=365)

        dates = split_dates_to_chunks(date_from, date_to, 0)

        self.login()

        table_name = self.cfg.destination.table_name or eshop_id

        table_def = self.create_out_table_definition(name=f'{table_name}.csv',
                                                     incremental=self.cfg.destination.load_type.is_incremental(),
                                                     primary_key=['eshop_id', 'date'])

        with (ElasticDictWriter(table_def.full_path, fieldnames=['eshop_id', 'date', 'pno', 'conversion_rates',
                                                                 'spend', 'aov', 'cpc', 'orders',
                                                                 'visits', 'transaction_revenue']) as writer):
            writer.writeheader()

            for date in dates:
                logging.info(f"Downloading data for date: {date['start_date']}")
                try:
                    stats = self.get_stats_for_date(self.session, date, eshop_id)
                    writer.writerow(stats)
                except TableNotFoundException as e:
                    logging.warning(f"Error while downloading data for date: {date['start_date']}: {e}")

        self.write_manifest(table_def)

    @backoff.on_exception(backoff.expo, TableNotFoundException, max_tries=3)
    def get_stats_for_date(self, session, date, eshop_id):
        if self.cfg.country == "cz":
            response = session.get('https://sluzby.heureka.cz/obchody/statistiky/'
                                   f'?from={date["start_date"]}&to={date["start_date"]}&shop={eshop_id}&cat=-4')

            columns_mapping = {
                'NÃ¡vÅ¡tÄ\x9bvy': 'visits',
                'CPC': 'cpc',
                'NÃ¡klady': 'spend',
                'KonverznÃ­ pomÄ\x9br': 'conversion_rates',
                'Obj': 'orders',
                'PrÅ¯mÄ\x9brnÃ¡ objednÃ¡vka': 'aov',
                'Obrat': 'transaction_revenue',
                'NÃ¡klady zÂ obratu': 'pno',
            }

        else:
            response = session.get('https://sluzby.heureka.sk/obchody/statistiky/'
                                   f'?from={date["start_date"]}&to={date["start_date"]}&shop={eshop_id}&cat=-4')

            columns_mapping = {
                'NÃ¡vÅ¡tevy': 'visits',
                'CPC': 'cpc',
                'NÃ¡klady': 'spend',
                'KonverznÃ½ pomer': 'conversion_rates',
                'Obj': 'orders',
                'PriemernÃ¡ objednÃ¡vka': 'aov',
                'Obrat': 'transaction_revenue',
                'NÃ¡klady zÂ obratu': 'pno',
            }

        try:
            html = HTMLParser(response.text)
            thead = html.css('thead tr')[1]
            tbody = html.css('tbody tr')[0]

            if not thead or not tbody:
                raise TableNotFoundException("Table structure not found")

            column_names = [th.text().strip() for th in thead.css('th')]
            values = [td.text().strip().replace('\xa0Kč', '').replace('€', '').replace('%', '').strip()
                      for td in tbody.css('td')]

            row = {'eshop_id': eshop_id, 'date': date["start_date"]}

            if values[0] == 'Celkem':
                logging.warning("No data available for the selected period")
            else:
                for column_name, value in zip(column_names, values):
                    if key := columns_mapping.get(column_name):
                        row[key] = value

            return row

        except Exception as e:
            self.login()
            logging.warning("Table not found, logging in again")
            raise TableNotFoundException(str(e))


"""
        Main entrypoint
"""
if __name__ == "__main__":
    try:
        comp = Component()
        # this triggers the run method by default and is controlled by the configuration.action parameter
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
