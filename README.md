Heureka
=============

Description

**Table of contents:**

[TOC]

Functionality notes
===================

**NOTICE!**
- The data from the past 30 days may be incomplete.
- The data of the previous day becomes available on the following day.
- Due to the introduction of mandatory consent for storing cookies, measured conversions and revenues on your e-shop may be lower by up to 30% than actual orders.

Configuration
=============

First, in the component configuration below, fill in the API key.
Then in the config row, specify the e-shop ID, date for which you want to retrieve the data, and destination table settings.

Output
======

**Summary table** columns:

eshop_id, date, pno, conversion_rates, spend, aov, cpc, orders, visits, transaction_revenue, visits_free, visits_bidded, visits_not_bidded, orders_free, orders_bidded, orders_not_bidded, revenue_free, revenue_bidded, revenue_not_bidded, spend_without_vat

**Detail table** columns (enabled via "Output Detailed Data" option):

eshop_id, date, product_card_id, product_name, shop_item_id, shop_item_name, click_source, satellite_name, on_bidded_position, portal_category_id, visits_total, visits_free, visits_bidded, visits_not_bidded, costs_with_vat_total, costs_with_vat_bidded, costs_with_vat_not_bidded, costs_without_vat_total, costs_without_vat_bidded, costs_without_vat_not_bidded, orders_total, orders_free, orders_bidded, orders_not_bidded, revenue_total, revenue_free, revenue_bidded, revenue_not_bidded

Development
-----------

If required, change local data folder (the `CUSTOM_FOLDER` placeholder) path to
your custom path in the `docker-compose.yml` file:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    volumes:
      - ./:/code
      - ./CUSTOM_FOLDER:/data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone this repository, init the workspace and run the component with following
command:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
git clone https://github.com/keboola/component-heureka component-heureka
cd component-heureka
docker-compose build
docker-compose run --rm dev
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the test suite and lint check using this command:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
docker-compose run --rm test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration
===========

For information about deployment and integration with KBC, please refer to the
[deployment section of developers
documentation](https://developers.keboola.com/extend/component/deployment/)
