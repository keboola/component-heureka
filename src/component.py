"""
Template Component main class.

"""
import logging
from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from configuration import Configuration
from requests_html import HTMLSession
from keboola.utils import parse_datetime_interval, split_dates_to_chunks
from keboola.csvwriter import ElasticDictWriter
import datetime


class Component(ComponentBase):

    def __init__(self):
        super().__init__()

    def _init_configuration(self) -> None:
        self.validate_configuration_parameters(Configuration.get_dataclass_required_parameters())
        self.cfg: Configuration = Configuration.load_from_dict(self.configuration.parameters)

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

        session = HTMLSession()

        if self.cfg.country == "cz":
            url = 'https://account.heureka.cz/auth/login?redirect_uri=https%3A%2F%2Fauth.heureka.cz%2Fapi%2Fopenidconnect%2Fauthorize%3Fclient_id%3Dheureka.cz%26response_type%3Dcode%26scope%3Dtenant%253Aheureka-group%2Bcookie%2Buserinfo%253A%252A%2Bprofile%253AHEU-CZ%26redirect_uri%3Dhttps%253A%252F%252Fsluzby.heureka.cz%252Fobchody%252F&client_id=heureka.cz'  # noqa
            data = {'email': self.cfg.credentials.email, 'password': self.cfg.credentials.pswd_password}

            session.post(url, data=data)
            response = session.get(
                'https://auth.heureka.cz/api/openidconnect/authorize?client_id=heureka.cz&response_type=code&scope=tenant%3Aheureka-group+cookie+userinfo%3A%2A+profile%3AHEU-CZ&redirect_uri=https%3A%2F%2Faccount.heureka.cz%2F')  # noqa

        elif self.cfg.country == "sk":
            # url = 'https://account.heureka.sk/auth/login?redirect_uri=https%3A%2F%2Fauth.heureka.sk%2Fapi%2Fopenidconnect%2Fauthorize%3Fclient_id%3Dheureka.sk%26response_type%3Dcode%26scope%3Dtenant%253Aheureka-group%2Bcookie%2Buserinfo%253A%252A%2Bprofile%253AHEU-SK%26redirect_uri%3Dhttps%253A%252F%252Fsluzby.heureka.sk%252Fobchody%252F&client_id=heureka.sk' # noqa
            url = 'https://account.heureka.sk/auth/login?client_id=heureka.sk'
            data = {'email': self.cfg.credentials.email, 'password': self.cfg.credentials.pswd_password}

            session.post(url, data=data)
            # response = session.get('https://auth.heureka.sk/api/openidconnect/authorize?client_id=heureka.sk&response_type=code&scope=tenant%3Aheureka-group+cookie+userinfo%3A%2A+profile%3AHEU-SK&redirect_uri=https%3A%2F%2Fwww.heureka.sk%2F')  # noqa
            response = session.get(
                'https://auth.heureka.sk/api/openidconnect/authorize?client_id=heureka.sk&response_type=code')
            # print(response.text)
        else:
            raise UserException("Country not supported")

        if response.status_code != 200:
            raise UserException(f"Login failed: {response.status_code}, {response.text}")

        logging.info("Login successful")

        table_name = self.cfg.destination.table_name or eshop_id

        table_def = self.create_out_table_definition(name=f'{table_name}.csv',
                                                     incremental=self.cfg.destination.load_type.is_incremental(),
                                                     primary_key=['eshop_id', 'date'])

        with (ElasticDictWriter(table_def.full_path, fieldnames=['eshop_id', 'date', 'pno', 'conversion_rates',
                                                                 'spend', 'aov', 'cpc', 'orders',
                                                                 'visits', 'transaction_revenue']) as writer):
            writer.writeheader()

            for date in dates:
                stats = self.get_stats_for_date(session, date, eshop_id)
                writer.writerow(stats)

        self.write_manifest(table_def)

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
            logging.debug(response.text)
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

        column_names = [th.text for th in response.html.find('thead', first=True).find('tr')[1].find('th')]
        table_body = response.html.find('tbody', first=True)

        if table_body:

            values = [value.text.replace('Â\xa0KÄ\x8d', '').replace('Â â\x82¬', '').replace('%', '')
                      .replace('Â', '').replace(' ', '').replace('&nbsp', '').replace(' ', '')
                      for value in table_body.find('tr')[0].find('td')]

            row = {'eshop_id': eshop_id, 'date': date["start_date"]}

            if values[0] == 'Celkem':
                logging.warning("No data available for the selected period")
            else:
                for column_name, value in zip(column_names, values):
                    if key := columns_mapping.get(column_name):
                        row[key] = value

            return row


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
