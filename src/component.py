"""
Template Component main class.

"""

import os
import tempfile
import logging
import datetime

from kbcstorage.files import Files
from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from configuration import Configuration
from requests_html import HTMLSession
from keboola.utils import parse_datetime_interval, split_dates_to_chunks
from keboola.csvwriter import ElasticDictWriter
from playwright.sync_api import sync_playwright
import backoff


class TableNotFoundException(Exception):
    pass


class Component(ComponentBase):
    def __init__(self):
        super().__init__()
        self.session = HTMLSession()

    def _init_configuration(self) -> None:
        self.validate_configuration_parameters(Configuration.get_dataclass_required_parameters())
        self.cfg: Configuration = Configuration.load_from_dict(self.configuration.parameters)

    def run(self):
        """
        Main execution code
        """

        self._init_configuration()

        if self.cfg.country not in ("cz", "sk"):
            raise UserException("Country not supported")

        eshop_id = self.cfg.report_settings.eshop_id
        date_from, date_to = parse_datetime_interval(
            self.cfg.report_settings.date_from, self.cfg.report_settings.date_to
        )

        if (datetime.datetime.now() - date_from).days > 365:
            print("Cannot get data older than 1 year, downloading data for the last 365 days.")
            date_from = datetime.datetime.now() - datetime.timedelta(days=365)

        dates = split_dates_to_chunks(date_from, date_to, 0)

        self.login()

        table_name = self.cfg.destination.table_name or eshop_id

        table_def = self.create_out_table_definition(
            name=f"{table_name}.csv",
            incremental=self.cfg.destination.load_type.is_incremental(),
            primary_key=["eshop_id", "date"],
        )

        with ElasticDictWriter(
            table_def.full_path,
            fieldnames=[
                "eshop_id",
                "date",
                "pno",
                "conversion_rates",
                "spend",
                "aov",
                "cpc",
                "orders",
                "visits",
                "transaction_revenue",
            ],
        ) as writer:
            writer.writeheader()

            for date in dates:
                logging.info(f"Downloading data for date: {date['start_date']}")
                try:
                    stats = self.get_stats_for_date(self.session, date, eshop_id)
                    writer.writerow(stats)
                except TableNotFoundException as e:
                    logging.warning(f"Error while downloading data for date: {date['start_date']}: {e}")

        self.write_manifest(table_def)

    @backoff.on_exception(backoff.expo, Exception, max_tries=3, giveup=lambda e: isinstance(e, UserException))
    def login(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = None
            try:
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(f"https://heureka.{self.cfg.country}")

                try:
                    page.click("#didomi-notice-agree-button")
                except Exception:
                    logging.info("No cookies popup")

                if self.cfg.country == "cz":
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.get_by_text("Administrace e-shopu").click()
                    page.wait_for_selector('button:has-text("Přihlásit se e-mailem")')
                    page.fill("#login-email", self.cfg.credentials.email)
                    page.fill("#login-password", self.cfg.credentials.pswd_password)
                    page.click('button:has-text("Přihlásit se e-mailem")')

                elif self.cfg.country == "sk":
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.get_by_text("Administrácia e-shopu").click()
                    page.wait_for_selector('button:has-text("Prihlásiť sa e-mailom")')
                    page.fill("#login-email", self.cfg.credentials.email)
                    page.fill("#login-password", self.cfg.credentials.pswd_password)
                    page.click('button:has-text("Prihlásiť sa e-mailom")')

                page.wait_for_load_state("networkidle")
                if page.query_selector("#login-email") or "sluzby.heureka" not in page.url:
                    raise UserException(f"Login failed - unexpected post-login page: {page.url}")

                for cookie in context.cookies():
                    self.session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])

            except Exception:
                if page:
                    logging.warning(f"Login failed at URL: {page.url}")
                    self.screenshot(page)
                raise
            finally:
                browser.close()

    @backoff.on_exception(backoff.expo, TableNotFoundException, max_tries=3)
    def get_stats_for_date(self, session, date, eshop_id):
        if self.cfg.country == "cz":
            response = session.get(
                "https://sluzby.heureka.cz/obchody/statistiky/"
                f"?from={date['start_date']}&to={date['start_date']}&shop={eshop_id}&cat=-4"
            )

            columns_mapping = {
                "NÃ¡vÅ¡tÄ\x9bvy": "visits",
                "CPC": "cpc",
                "NÃ¡klady": "spend",
                "KonverznÃ­ pomÄ\x9br": "conversion_rates",
                "Obj": "orders",
                "PrÅ¯mÄ\x9brnÃ¡ objednÃ¡vka": "aov",
                "Obrat": "transaction_revenue",
                "NÃ¡klady zÂ obratu": "pno",
            }

        else:
            response = session.get(
                "https://sluzby.heureka.sk/obchody/statistiky/"
                f"?from={date['start_date']}&to={date['start_date']}&shop={eshop_id}&cat=-4"
            )

            columns_mapping = {
                "NÃ¡vÅ¡tevy": "visits",
                "CPC": "cpc",
                "NÃ¡klady": "spend",
                "KonverznÃ½ pomer": "conversion_rates",
                "Obj": "orders",
                "PriemernÃ¡ objednÃ¡vka": "aov",
                "Obrat": "transaction_revenue",
                "NÃ¡klady zÂ obratu": "pno",
            }

        try:
            column_names = [th.text for th in response.html.find("thead", first=True).find("tr")[1].find("th")]
            table_body = response.html.find("tbody", first=True)

            if table_body:
                values = [
                    value.text.replace("Â\xa0KÄ\x8d", "")
                    .replace("Â â\x82¬", "")
                    .replace("%", "")
                    .replace("Â", "")
                    .replace(" ", "")
                    .replace("&nbsp", "")
                    .replace(" ", "")
                    for value in table_body.find("tr")[0].find("td")
                ]

                row = {"eshop_id": eshop_id, "date": date["start_date"]}

                if values[0] == "Celkem":
                    logging.warning("No data available for the selected period")
                else:
                    for column_name, value in zip(column_names, values):
                        if key := columns_mapping.get(column_name):
                            row[key] = value

                return row

        except AttributeError as e:
            logging.warning("Table not found, saving response and logging in again")
            self._save_response_artifact(response)
            self.login()
            raise TableNotFoundException(e)

    def _save_response_artifact(self, response):
        filename = f"heureka-debug-response-{datetime.datetime.now().strftime('%H%M%S')}.html"
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(response.text)
            tmp_path = tmp.name
        try:
            self._store_sapi_artifact(tmp_path, filename)
            logging.info(f"Response HTML saved as artifact: {filename}")
        except Exception as e:
            logging.warning(f"Failed to save response artifact: {e}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def screenshot(self, page):
        filename = f"heureka-debug-screen-{datetime.datetime.now().strftime('%H%M%S')}.png"
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            page.screenshot(path=tmp_path, timeout=5000)
            self._store_sapi_artifact(tmp_path, filename)
        except Exception as e:
            logging.warning(f"Failed to save screenshot artifact: {e}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _store_sapi_artifact(self, source_file_path: str, filename: str) -> None:
        tags = ["kds-team.ex-heureka", f"runId:{self.environment_variables.run_id or ''}"]
        files = Files(self.environment_variables.url, self.environment_variables.token)
        file_id = files.upload_file(source_file_path, tags=tags, is_permanent=False)
        logging.info(f"Screenshot uploaded as SAPI artifact, file ID: {file_id}")


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
