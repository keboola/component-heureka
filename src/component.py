"""
Template Component main class.

"""
import os
import inspect
from pathlib import Path
import logging
from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from configuration import Configuration
from keboola.utils import parse_datetime_interval, split_dates_to_chunks
from keboola.csvwriter import ElasticDictWriter
import datetime
from playwright.sync_api import sync_playwright, TimeoutError
from bs4 import BeautifulSoup
import backoff


class TableNotFoundException(Exception):
    pass


class CloudflareBlockedException(Exception):
    pass


class Component(ComponentBase):

    def __init__(self):
        super().__init__()
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None

    def _init_configuration(self) -> None:
        self.validate_configuration_parameters(Configuration.get_dataclass_required_parameters())
        self.cfg: Configuration = Configuration.load_from_dict(self.configuration.parameters)

    def run(self):
        self._init_configuration()

        if self.cfg.country not in ("cz", "sk"):
            raise UserException("Country not supported")

        eshop_id = self.cfg.report_settings.eshop_id
        date_from, date_to = parse_datetime_interval(self.cfg.report_settings.date_from,
                                                     self.cfg.report_settings.date_to)

        if (datetime.datetime.now() - date_from).days > 365:
            print("Cannot get data older than 1 year, downloading data for the last 365 days.")
            date_from = datetime.datetime.now() - datetime.timedelta(days=365)

        dates = split_dates_to_chunks(date_from, date_to, 0)

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=False)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)

        try:
            self.login()

            table_name = self.cfg.destination.table_name or eshop_id

            table_def = self.create_out_table_definition(name=f'{table_name}.csv',
                                                         incremental=self.cfg.destination.load_type.is_incremental(),
                                                         primary_key=['eshop_id', 'date'])

            with (ElasticDictWriter(table_def.full_path, fieldnames=['eshop_id', 'date', 'pno', 'conversion_rates',
                                                                     'spend', 'aov', 'cpc', 'orders',
                                                                     'visits', 'transaction_revenue']) as writer):
                writer.writeheader()

                consecutive_failures = 0
                for date in dates:
                    logging.info(f"Downloading data for date: {date['start_date']}")
                    try:
                        stats = self.get_stats_for_date(date, eshop_id)
                        writer.writerow(stats)
                        consecutive_failures = 0
                    except TableNotFoundException as e:
                        logging.warning(f"Error while downloading data for date: {date['start_date']}: {e}")
                        consecutive_failures += 1
                        if consecutive_failures >= 5:
                            raise UserException(
                                "Failed to download data for 5 consecutive dates. "
                                "The target website may be blocking requests or the page structure has changed."
                            )
                    except CloudflareBlockedException:
                        raise UserException(
                            "The target website is actively blocking requests via Cloudflare. "
                            "Please try again later or contact support."
                        )

            self.write_manifest(table_def)
        finally:
            self.browser.close()
            self.playwright.stop()

    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    def login(self):
        try:
            headers = self.page.goto(f'https://heureka.{self.cfg.country}').headers

            try:
                self.page.click('#didomi-notice-agree-button')
            except Exception as e:
                logging.info(f"No cookies popup - {e}")

            if self.cfg.country == "cz":
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                self.page.get_by_text('Administrace e-shopu').click()
                self.page.wait_for_selector('button:has-text("Přihlásit se e-mailem")')
                self.page.fill('#login-email', self.cfg.credentials.email)
                self.page.fill('#login-password', self.cfg.credentials.pswd_password)
                self.page.click('button:has-text("Přihlásit se e-mailem")')

            elif self.cfg.country == "sk":
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                self.page.get_by_text('Administrácia e-shopu').click()
                self.page.wait_for_selector('button:has-text("Prihlásiť sa e-mailom")')
                self.page.fill('#login-email', self.cfg.credentials.email)
                self.page.fill('#login-password', self.cfg.credentials.pswd_password)
                self.page.click('button:has-text("Prihlásiť sa e-mailom")')

            self.page.wait_for_load_state('networkidle')
            login_url = self.page.url
            logging.info(f"Login completed, now at: {login_url}")

        except TimeoutError:
            logging.warning(f"Can't login saving screenshot to artifacts,"
                            f" Cloudflare Ray ID: {headers.get('cf-ray') if headers else 'headers not found'}")
            self.screenshot(self.page)
            raise UserException("The component was unable to log in due to an unknown error."
                                "Please contact our support team for assistance.")

    @backoff.on_exception(backoff.expo, TableNotFoundException, max_tries=3)
    def get_stats_for_date(self, date, eshop_id):
        if self.cfg.country == "cz":
            url = ('https://sluzby.heureka.cz/obchody/statistiky/'
                   f'?from={date["start_date"]}&to={date["start_date"]}&shop={eshop_id}&cat=-4')

            columns_mapping = {
                'Návštěvy': 'visits',
                'CPC': 'cpc',
                'Náklady': 'spend',
                'Konverzní poměr': 'conversion_rates',
                'Obj': 'orders',
                'Průměrná objednávka': 'aov',
                'Obrat': 'transaction_revenue',
                'Náklady z obratu': 'pno',
            }

        else:
            url = ('https://sluzby.heureka.sk/obchody/statistiky/'
                   f'?from={date["start_date"]}&to={date["start_date"]}&shop={eshop_id}&cat=-4')

            columns_mapping = {
                'Návštevy': 'visits',
                'CPC': 'cpc',
                'Náklady': 'spend',
                'Konverzný pomer': 'conversion_rates',
                'Obj': 'orders',
                'Priemerná objednávka': 'aov',
                'Obrat': 'transaction_revenue',
                'Náklady z obratu': 'pno',
            }

        try:
            self.page.goto(url, wait_until='networkidle')
            current_url = self.page.url
            title = self.page.title()
            logging.info(f"Stats page loaded: url={current_url} title={title}")
            try:
                self.page.wait_for_selector('thead', timeout=15000)
            except TimeoutError:
                current_url = self.page.url
                title = self.page.title()
                logging.warning(
                    f"thead not found after 15s, url={current_url} title={title}"
                )
                self.screenshot(self.page)
                html_content = self.page.content()
                soup_dbg = BeautifulSoup(html_content, 'lxml')
                body = soup_dbg.find('body')
                body_text = body.get_text(separator=' ', strip=True)[:500] if body else 'NO BODY'
                logging.warning(f"Page body text: {body_text}")
                tables = soup_dbg.find_all('table')
                logging.warning(f"Tables found: {len(tables)}")
                for i, t in enumerate(tables[:3]):
                    logging.warning(f"Table {i} classes={t.get('class')} id={t.get('id')}")
                if 'cf-challenge' in html_content or 'Checking your browser' in html_content:
                    logging.error("Cloudflare challenge detected on stats page")
                    raise CloudflareBlockedException("Cloudflare is blocking stats page requests")
                self.login()
                raise TableNotFoundException(f"Stats table not found: url={current_url}")
            html_content = self.page.content()
            soup = BeautifulSoup(html_content, 'lxml')
            thead = soup.find('thead')

            header_rows = thead.find_all('tr')
            if len(header_rows) < 2:
                logging.warning(f"Unexpected thead structure: {len(header_rows)} rows")
                self.screenshot(self.page)
                raise TableNotFoundException(
                    f"Unexpected table header: {len(header_rows)} rows"
                )
            column_names = [th.get_text() for th in header_rows[1].find_all('th')]
            table_body = soup.find('tbody')

            if table_body:

                values = [value.get_text().replace('\xa0Kč', '').replace('\xa0€', '').replace('%', '')
                          .replace('\xa0', '').replace(' ', '').replace('&nbsp', '')
                          for value in table_body.find_all('tr')[0].find_all('td')]

                row = {'eshop_id': eshop_id, 'date': date["start_date"]}

                if values[0] == 'Celkem':
                    logging.warning("No data available for the selected period")
                else:
                    for column_name, value in zip(column_names, values):
                        if key := columns_mapping.get(column_name):
                            row[key] = value

                return row

        except AttributeError as e:
            self.login()
            logging.warning("Table not found, logging in again")
            raise TableNotFoundException(e)

    def screenshot(self, page):
        artifact_out_path = Path.joinpath(Path(self.data_folder_path), 'artifacts/out/current/')
        os.makedirs(artifact_out_path, exist_ok=True)
        caller_line = inspect.currentframe().f_back.f_lineno
        file_path = Path.joinpath(artifact_out_path, f"heureka-debug-screen-{caller_line}.png")
        page.screenshot(path=file_path)


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
