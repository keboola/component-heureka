import logging
import datetime

import backoff
import requests
from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from keboola.csvwriter import ElasticDictWriter
from keboola.utils import parse_datetime_interval, split_dates_to_chunks
from pydantic import ValidationError

from configuration import Configuration, DecimalSeparator

API_BASE_URL = "https://api.heureka.group/v1"

SUMMARY_COLUMNS = [
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
    "visits_free",
    "visits_bidded",
    "visits_not_bidded",
    "orders_free",
    "orders_bidded",
    "orders_not_bidded",
    "revenue_free",
    "revenue_bidded",
    "revenue_not_bidded",
    "spend_without_vat",
]

DETAIL_COLUMNS = [
    "eshop_id",
    "date",
    "product_card_id",
    "product_name",
    "shop_item_id",
    "shop_item_name",
    "click_source",
    "satellite_name",
    "on_bidded_position",
    "portal_category_id",
    "visits_total",
    "visits_free",
    "visits_bidded",
    "visits_not_bidded",
    "costs_with_vat_total",
    "costs_with_vat_bidded",
    "costs_with_vat_not_bidded",
    "costs_without_vat_total",
    "costs_without_vat_bidded",
    "costs_without_vat_not_bidded",
    "orders_total",
    "orders_free",
    "orders_bidded",
    "orders_not_bidded",
    "revenue_total",
    "revenue_free",
    "revenue_bidded",
    "revenue_not_bidded",
]


class Component(ComponentBase):
    def __init__(self):
        super().__init__()
        self.cfg = self._load_configuration()

    def _load_configuration(self) -> Configuration:
        try:
            return Configuration(**self.configuration.parameters)
        except ValidationError as e:
            raise UserException(f"Invalid configuration: {e}") from e

    def run(self):
        eshop_id = self.cfg.report_settings.eshop_id
        date_from, date_to = parse_datetime_interval(
            self.cfg.report_settings.date_from, self.cfg.report_settings.date_to
        )

        if (datetime.datetime.now() - date_from).days > 365:
            logging.info("Cannot get data older than 1 year, downloading data for the last 365 days.")
            date_from = datetime.datetime.now() - datetime.timedelta(days=365)

        dates = split_dates_to_chunks(date_from, date_to, 0)
        table_name = self.cfg.destination.table_name or eshop_id

        summary_def = self.create_out_table_definition(
            name=f"{table_name}.csv",
            incremental=self.cfg.destination.load_type.is_incremental(),
            primary_key=["eshop_id", "date"],
        )

        detail_def = None
        if self.cfg.report_settings.output_detail:
            detail_def = self.create_out_table_definition(
                name=f"{table_name}_detail.csv",
                incremental=self.cfg.destination.load_type.is_incremental(),
                primary_key=["eshop_id", "date", "product_card_id", "click_source"],
            )

        use_decimal_comma = self.cfg.destination.decimal_separator == DecimalSeparator.comma

        with ElasticDictWriter(summary_def.full_path, fieldnames=SUMMARY_COLUMNS) as summary_writer:
            summary_writer.writeheader()

            detail_writer = None
            try:
                if detail_def:
                    detail_writer = ElasticDictWriter(detail_def.full_path, fieldnames=DETAIL_COLUMNS)
                    detail_writer.__enter__()
                    detail_writer.writeheader()

                for date_chunk in dates:
                    date_str = date_chunk["start_date"]
                    logging.info(f"Fetching data for date: {date_str}")

                    conversions = self._fetch_conversions(date_str)

                    if not conversions:
                        logging.warning(f"No conversion data for {date_str}")
                        continue

                    row = self._aggregate_daily(eshop_id, date_str, conversions)
                    summary_writer.writerow(self._format_row(row, use_decimal_comma))

                    if detail_writer:
                        for conv in conversions:
                            detail_writer.writerow(
                                self._format_row(self._flatten_conversion(eshop_id, date_str, conv), use_decimal_comma)
                            )
            finally:
                if detail_writer:
                    detail_writer.__exit__(None, None, None)

        self.write_manifest(summary_def)
        if detail_def:
            self.write_manifest(detail_def)

    @staticmethod
    def _format_row(row: dict, use_decimal_comma: bool) -> dict:
        if not use_decimal_comma:
            return row
        return {k: str(v).replace(".", ",") if isinstance(v, float) else v for k, v in row.items()}

    @backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=3)
    def _fetch_conversions(self, date_str: str) -> list[dict]:
        headers = {"x-heureka-api-key": self.cfg.credentials.api_key}
        response = requests.get(
            f"{API_BASE_URL}/reports/conversions",
            params={"date": date_str},
            headers=headers,
            timeout=60,
        )
        if response.status_code == 401:
            raise UserException("Authentication failed. Check your API key.")
        if response.status_code == 403:
            raise UserException("Access forbidden. Check your API key permissions.")
        response.raise_for_status()
        return response.json().get("conversions", [])

    @staticmethod
    def _aggregate_daily(eshop_id: str, date_str: str, conversions: list[dict]) -> dict:
        visits = sum(c["visits"]["total"] for c in conversions)
        visits_free = sum(c["visits"]["free"] for c in conversions)
        visits_bidded = sum(c["visits"]["bidded"] for c in conversions)
        visits_not_bidded = sum(c["visits"]["not_bidded"] for c in conversions)

        spend = sum(c["costs_with_vat"]["total"] for c in conversions)
        spend_without_vat = sum(c["costs_without_vat"]["total"] for c in conversions)

        orders = sum(c["orders"]["total"] for c in conversions)
        orders_free = sum(c["orders"]["free"] for c in conversions)
        orders_bidded = sum(c["orders"]["bidded"] for c in conversions)
        orders_not_bidded = sum(c["orders"]["not_bidded"] for c in conversions)

        revenue = sum(c["revenue"]["total"] for c in conversions)
        revenue_free = sum(c["revenue"]["free"] for c in conversions)
        revenue_bidded = sum(c["revenue"]["bidded"] for c in conversions)
        revenue_not_bidded = sum(c["revenue"]["not_bidded"] for c in conversions)

        cpc = round(spend / visits, 2) if visits > 0 else 0
        aov = round(revenue / orders, 2) if orders > 0 else 0
        conversion_rates = round((orders / visits) * 100, 2) if visits > 0 else 0
        pno = round((spend / revenue) * 100, 2) if revenue > 0 else 0

        return {
            "eshop_id": eshop_id,
            "date": date_str,
            "visits": visits,
            "spend": round(spend, 2),
            "orders": orders,
            "transaction_revenue": round(revenue, 2),
            "cpc": cpc,
            "aov": aov,
            "conversion_rates": conversion_rates,
            "pno": pno,
            "visits_free": visits_free,
            "visits_bidded": visits_bidded,
            "visits_not_bidded": visits_not_bidded,
            "orders_free": orders_free,
            "orders_bidded": orders_bidded,
            "orders_not_bidded": orders_not_bidded,
            "revenue_free": round(revenue_free, 2),
            "revenue_bidded": round(revenue_bidded, 2),
            "revenue_not_bidded": round(revenue_not_bidded, 2),
            "spend_without_vat": round(spend_without_vat, 2),
        }

    @staticmethod
    def _flatten_conversion(eshop_id: str, date_str: str, conv: dict) -> dict:
        return {
            "eshop_id": eshop_id,
            "date": conv.get("date") or date_str,
            "product_card_id": conv.get("product_card_id"),
            "product_name": conv.get("product_name"),
            "shop_item_id": conv.get("shop_item", {}).get("id"),
            "shop_item_name": conv.get("shop_item", {}).get("name"),
            "click_source": conv.get("click_source"),
            "satellite_name": conv.get("satellite_name"),
            "on_bidded_position": conv.get("on_bidded_position"),
            "portal_category_id": conv.get("portal_category", {}).get("id"),
            "visits_total": conv["visits"]["total"],
            "visits_free": conv["visits"]["free"],
            "visits_bidded": conv["visits"]["bidded"],
            "visits_not_bidded": conv["visits"]["not_bidded"],
            "costs_with_vat_total": conv["costs_with_vat"]["total"],
            "costs_with_vat_bidded": conv["costs_with_vat"]["bidded"],
            "costs_with_vat_not_bidded": conv["costs_with_vat"]["not_bidded"],
            "costs_without_vat_total": conv["costs_without_vat"]["total"],
            "costs_without_vat_bidded": conv["costs_without_vat"]["bidded"],
            "costs_without_vat_not_bidded": conv["costs_without_vat"]["not_bidded"],
            "orders_total": conv["orders"]["total"],
            "orders_free": conv["orders"]["free"],
            "orders_bidded": conv["orders"]["bidded"],
            "orders_not_bidded": conv["orders"]["not_bidded"],
            "revenue_total": conv["revenue"]["total"],
            "revenue_free": conv["revenue"]["free"],
            "revenue_bidded": conv["revenue"]["bidded"],
            "revenue_not_bidded": conv["revenue"]["not_bidded"],
        }


"""
        Main entrypoint
"""
if __name__ == "__main__":
    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
