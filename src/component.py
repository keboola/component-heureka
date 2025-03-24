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


class LoginError(Exception):
    pass


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
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)  # Changed to headless=True for better stability
                if not browser:
                    raise LoginError("Failed to launch browser")

                context = await browser.new_context()
                if not context:
                    raise LoginError("Failed to create browser context")

                page = await context.new_page()
                if not page:
                    raise LoginError("Failed to create new page")

                await page.set_default_timeout(20000)  # Increased timeout for better reliability

                # Navigate to the page and wait for load state
                response = await page.goto(f'https://heureka.{self.cfg.country}')
                if not response or not response.ok:
                    raise LoginError(f"Failed to load page: {response.status if response else 'No response'}")
                await page.wait_for_load_state('networkidle')

                try:
                    # Wait for cookie button with timeout
                    cookie_button = await page.wait_for_selector('#didomi-notice-agree-button', timeout=5000)
                    if cookie_button:
                        await cookie_button.click()
                except Exception:
                    logging.info("No cookies popup or failed to click")

                if self.cfg.country == "cz":
                    # Scroll and wait for elements to be visible
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_load_state('networkidle')

                    admin_link = await page.get_by_text('Administrace e-shopu').wait_for(state='visible', timeout=10000)
                    if not admin_link:
                        raise LoginError("Could not find admin link")
                    await admin_link.click()

                    await page.wait_for_selector('button:has-text("Přihlásit se e-mailem")', timeout=20000)

                    email_input = await page.wait_for_selector('#login-email')
                    password_input = await page.wait_for_selector('#login-password')
                    login_button = await page.wait_for_selector('button:has-text("Přihlásit se e-mailem")')

                    if not all([email_input, password_input, login_button]):
                        raise LoginError("Login form elements not found")

                    await email_input.fill(self.cfg.credentials.email)
                    await password_input.fill(self.cfg.credentials.pswd_password)
                    await login_button.click()

                    # Wait for navigation after login
                    await page.wait_for_load_state('networkidle')

                elif self.cfg.country == "sk":
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_load_state('networkidle')

                    admin_link = await page.get_by_text('Administrácia e-shopu').wait_for(
                        state='visible',
                        timeout=10000
                    )
                    if not admin_link:
                        raise LoginError("Could not find admin link")
                    await admin_link.click()

                    await page.wait_for_selector('button:has-text("Prihlásiť sa e-mailom")', timeout=20000)

                    email_input = await page.wait_for_selector('#login-email')
                    password_input = await page.wait_for_selector('#login-password')
                    login_button = await page.wait_for_selector('button:has-text("Prihlásiť sa e-mailom")')

                    if not all([email_input, password_input, login_button]):
                        raise LoginError("Login form elements not found")

                    await email_input.fill(self.cfg.credentials.email)
                    await password_input.fill(self.cfg.credentials.pswd_password)
                    await login_button.click()

                    # Wait for navigation after login
                    await page.wait_for_load_state('networkidle')
                else:
                    raise UserException("Country not supported")

                cookies = await context.cookies()
                if not cookies:
                    raise LoginError("No cookies found after login")

                for cookie in cookies:
                    self.session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])

                await browser.close()

        except Exception as e:
            raise LoginError(f"Login failed: {str(e)}")

    @backoff.on_exception(backoff.expo, (LoginError, Exception), max_tries=3)
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
