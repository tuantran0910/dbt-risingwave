import time
from typing import Any, Dict, Optional

from dbt.adapters.base.meta import available
from dbt.adapters.events.logging import AdapterLogger
from dbt.adapters.postgres.impl import PostgresAdapter

from dbt.adapters.risingwave.connections import RisingWaveConnectionManager
from dbt.adapters.risingwave.relation import RisingWaveRelation

logger = AdapterLogger("RisingWave")


def _retry_call(operation, attempts: int, delay: float, label: str):
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as e:
            if attempt < attempts - 1:
                wait = delay * (2**attempt)
                logger.warning(
                    f"{label} failed (attempt {attempt + 1}/{attempts}): {e}. "
                    f"Retrying in {wait:.1f}s..."
                )
                time.sleep(wait)
            else:
                raise


class RisingWaveAdapter(PostgresAdapter):
    ConnectionManager = RisingWaveConnectionManager
    Relation = RisingWaveRelation

    def _link_cached_relations(self, manifest):
        # lack of `pg_depend`, `pg_rewrite`
        pass

    @available
    @classmethod
    def sleep(cls, seconds):
        time.sleep(seconds)

    @available
    def persist_iceberg_docs(
        self,
        relation,
        model: Dict[str, Any],
        for_relation: bool = True,
        for_columns: bool = True,
    ) -> None:
        meta: Dict[str, Any] = model.get("config", {}).get("meta", {})
        if not meta.get("iceberg"):
            return

        # Profile-level defaults, overridden by model meta config
        profile_catalog_cfg: Dict[str, Any] = {}
        try:
            profile_catalog_cfg = dict(self.config.credentials.iceberg_catalog or {})
        except AttributeError:
            pass

        catalog_props: Dict[str, Any] = {
            **profile_catalog_cfg,
            **dict(meta.get("iceberg_catalog") or {}),
        }
        if not catalog_props:
            logger.warning(
                f"iceberg=true set on model '{relation.identifier}' but no "
                "iceberg_catalog config found in profile or model meta. "
                "Skipping Iceberg doc sync."
            )
            return

        retry_attempts: int = int(catalog_props.pop("iceberg_retry_attempts", 3))
        retry_delay: float = float(catalog_props.pop("iceberg_retry_delay", 1.0))

        # `name` is a logical label for the catalog, not a catalog property
        catalog_name: str = str(catalog_props.pop("name", "default"))
        namespace: Optional[str] = meta.get("iceberg_namespace")
        table_name: Optional[str] = meta.get("iceberg_table")
        if not namespace or not table_name:
            missing = [
                k
                for k, v in [
                    ("iceberg_namespace", namespace),
                    ("iceberg_table", table_name),
                ]
                if not v
            ]
            logger.warning(
                f"iceberg=true set on model '{relation.identifier}' but "
                f"required meta key(s) are missing: {missing}. "
                "Skipping Iceberg doc sync."
            )
            return

        try:
            from pyiceberg.catalog import load_catalog
        except ImportError:
            logger.warning(
                "PyIceberg is not installed. "
                "Install with: pip install 'dbt-risingwave[iceberg]' or "
                "pip install 'dbt-risingwave[iceberg-gcp]' or "
                "pip install 'dbt-risingwave[iceberg-aws]' or "
                "pip install 'dbt-risingwave[iceberg-azure]'. "
                "Skipping Iceberg doc sync."
            )
            return

        try:
            catalog = _retry_call(
                lambda: load_catalog(catalog_name, **catalog_props),
                attempts=retry_attempts,
                delay=retry_delay,
                label=f"Connecting to Iceberg catalog '{catalog_name}'",
            )
        except Exception as e:
            logger.warning(
                f"Failed to connect to Iceberg catalog '{catalog_name}' after "
                f"{retry_attempts} attempt(s): {e}. Skipping Iceberg doc sync."
            )
            return

        try:
            table = _retry_call(
                lambda: catalog.load_table((namespace, table_name)),
                attempts=retry_attempts,
                delay=retry_delay,
                label=f"Loading Iceberg table '{namespace}.{table_name}'",
            )
        except Exception as e:
            logger.warning(
                f"Iceberg table '{namespace}.{table_name}' not found or inaccessible after "
                f"{retry_attempts} attempt(s): {e}. Skipping Iceberg doc sync."
            )
            return

        description: str = model.get("description", "")
        columns: Dict[str, Any] = model.get("columns", {})

        iceberg_cols = {field.name for field in table.schema().fields}
        cols_in_both: Dict[str, str] = (
            {
                col_name: col_info.get("description", "")
                for col_name, col_info in columns.items()
                if col_name in iceberg_cols
            }
            if for_columns and columns
            else {}
        )

        def _commit():
            with table.transaction() as txn:
                if for_relation:
                    if description:
                        txn.set_properties({"comment": description})
                    else:
                        # Description was removed — clear the Iceberg table comment
                        txn.remove_properties("comment")
                if cols_in_both:
                    with txn.update_schema() as schema_update:
                        for col_name, col_doc in cols_in_both.items():
                            # Pass empty string to clear a previously-set doc
                            schema_update.update_column(col_name, doc=col_doc)

        try:
            _retry_call(
                _commit,
                attempts=retry_attempts,
                delay=retry_delay,
                label=f"Committing docs to Iceberg table '{namespace}.{table_name}'",
            )
        except Exception as e:
            logger.warning(
                f"Failed to sync docs to Iceberg table '{namespace}.{table_name}' after "
                f"{retry_attempts} attempt(s): {e}. RisingWave SQL comments were still applied."
            )
            return

        if for_relation:
            action = "set" if description else "cleared"
            logger.info(
                f"Iceberg table comment {action} for '{namespace}.{table_name}'."
            )

        if cols_in_both:
            logger.info(
                f"Iceberg column docs synced for '{namespace}.{table_name}': "
                f"{sorted(cols_in_both.keys())}."
            )
