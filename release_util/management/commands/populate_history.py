"""
Management command to populate initial history.
"""
import logging
import time
from datetime import datetime
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from simple_history.utils import get_history_model_for_model

get_model = apps.get_model

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Populate initial history for models using django-simple-history.
    Example usage:
    $ ./manage.py lms generate_initial_history --models organizations.organization entitlements.courseentitlement --batchsize 1000 --sleep_between 1 --settings=devstack
    """

    help = (
        "Populates the corresponding historical records with"
        "the current state of records which do not have a historical record yet"
    )

    DEFAULT_BATCH_SIZE = 200
    DEFAULT_SLEEP_BETWEEN_INSERTS = 1
    DATE = datetime.today().strftime('%Y-%m-%d')
    HISTORY_USER_ID = 'NULL'
    HISTORY_CHANGE_REASON = 'initial history population'

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)

        parser.add_argument("--models", nargs="*", type=str)

        parser.add_argument(
            '--sleep_between',
            default=self.DEFAULT_SLEEP_BETWEEN_INSERTS,
            type=float,
            help='Seconds to sleep between chunked inserts.'
        )

        parser.add_argument(
            "--batchsize",
            action="store",
            default=self.DEFAULT_BATCH_SIZE,
            type=int,
            help="Maximum number of history rows to insert in each batch.",
        )

    def handle(self, *args, **options):
        model_strings = options.get("models", [])
        increment = options['batchsize']
        sleep_between = options['sleep_between']

        for model_string in model_strings:
            app_label, model = model_string.split(".", 1)
            model = get_model(app_label, model)
            history_model = get_history_model_for_model(model)

            table = model._meta.db_table
            historical_table = history_model._meta.db_table

            with connection.cursor() as cursor:
                query = u"""
                    SELECT
                        MIN(t.id),
                        MAX(t.id)
                    FROM {table} t
                    LEFT JOIN {historical_table}
                        ON t.id = {historical_table}.id
                    WHERE {historical_table}.id IS NULL
                    """.format(
                        table=table,
                        historical_table=historical_table,
                )
                cursor.execute(query)
                start_id, end_id = cursor.fetchone()
                if not start_id or not end_id:
                    log.info(u"No records with missing historical records for table %s - skipping.", table)
                    continue
                query = u"""
                    SELECT
                        column_name
                    FROM information_schema.columns
                    WHERE table_name='{}'
                    ORDER BY ordinal_position
                    """.format(table)
                cursor.execute(query)
                columns = [column[0] for column in cursor.fetchall()]
            while True:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        log.info(
                            u"Inserting historical records for %s starting with id %s to %s",
                            table,
                            start_id,
                            start_id + increment - 1,
                        )
                        # xss-lint: disable=python-wrap-html
                        query = u"""
                            INSERT INTO {historical_table}(
                                {insert_columns},history_date,history_change_reason,history_type,history_user_id
                            )
                            SELECT {select_columns},'{history_date}','{history_change_reason}', '+', {history_user_id}
                            FROM {table} t
                            LEFT JOIN {historical_table}
                                ON t.id={historical_table}.id
                            WHERE {historical_table}.id IS NULL
                                AND t.id >= {start_id}
                                AND t.id < {end_id}
                            """.format(
                                table=table,
                                historical_table=historical_table,
                                insert_columns=','.join(columns),
                                select_columns=','.join(['t.{}'.format(c) for c in columns]),
                                history_date=self.DATE,
                                history_change_reason=self.HISTORY_CHANGE_REASON,
                                history_user_id=self.HISTORY_USER_ID,
                                start_id=start_id,
                                end_id=start_id + increment
                        )
                        log.info(query)
                        count = cursor.execute(query)
                        log.info(u"Inserted %s historical records", count)
                start_id += increment
                log.info(u"Sleeping %s seconds...", sleep_between)
                time.sleep(sleep_between)
                if start_id > end_id:
                    break
