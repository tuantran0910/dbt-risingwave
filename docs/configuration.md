# Configuration

This page documents the adapter-specific settings supported by `dbt-risingwave`.

## Profile Configuration

The basic dbt profile looks like this:

```yaml
default:
  outputs:
    dev:
      type: risingwave
      host: 127.0.0.1
      user: root
      pass: ""
      dbname: dev
      port: 4566
      schema: public
  target: dev
```

The adapter also supports several RisingWave session settings directly in the profile. When these are present, `dbt-risingwave` issues the corresponding `SET` statements as soon as the connection opens.

```yaml
default:
  outputs:
    dev:
      type: risingwave
      host: 127.0.0.1
      user: root
      pass: ""
      dbname: dev
      port: 4566
      schema: public
      streaming_parallelism: 2
      streaming_parallelism_for_backfill: 2
      streaming_max_parallelism: 8
  target: dev
```

Supported adapter-specific profile keys:

| Key | Description |
| --- | --- |
| `streaming_parallelism` | Sets `SET streaming_parallelism = ...` for the session. |
| `streaming_parallelism_for_backfill` | Sets `SET streaming_parallelism_for_backfill = ...` for the session. |
| `streaming_max_parallelism` | Sets `SET streaming_max_parallelism = ...` for the session. |

## Model Configuration

The adapter also supports RisingWave-specific model configs. These can be set in `config(...)` blocks or in `dbt_project.yml`.

### Schema Authorization

Use `schema_authorization` to set the owner of schemas created by dbt:

```sql
{{ config(materialized='table', schema_authorization='my_role') }}

select *
from ...
```

Or globally:

```yaml
models:
  my_project:
    +schema_authorization: my_role
```

Generated SQL:

```sql
create schema if not exists <schema_name> authorization "my_role"
```

### SQL Header

Use `sql_header` to prepend custom SQL before the main statement:

```sql
{{ config(
    materialized='table',
    sql_header='set query_mode = local;'
) }}

select *
from ...
```

The adapter appends its own RisingWave session settings after the custom header when those configs are present.

### Streaming Parallelism Per Model

You can override the session-level streaming settings for an individual model:

```sql
{{ config(
    materialized='materialized_view',
    streaming_parallelism=2,
    streaming_parallelism_for_backfill=2,
    streaming_max_parallelism=8
) }}

select *
from {{ ref('events') }}
```

These values are emitted in the SQL header before the model DDL runs.

### Background DDL

`dbt-risingwave` supports opting into RisingWave background DDL for these paths:

- `materialized_view`
- `table`
- `sink`
- index creation triggered by model `indexes` config

Enable it per model:

```sql
{{ config(
    materialized='materialized_view',
    background_ddl=true
) }}

select *
from {{ ref('events') }}
```

Or set it in `dbt_project.yml`:

```yaml
models:
  my_project:
    +background_ddl: true
```

How it works:

- The adapter sets `background_ddl = true` before running supported DDL.
- After submitting the DDL, the adapter issues RisingWave `WAIT`.
- dbt does not continue to downstream models, hooks, or tests until `WAIT` returns.

Caveat:

- RisingWave `WAIT` waits for all background creating jobs, not only the job started by the current dbt model. If other background DDL is running in the same cluster, the dbt node may wait on that work too.

### Index Configuration Changes

`materialized_view`, `table`, and `table_with_connector` support dbt's `on_configuration_change` behavior for index changes.

```sql
{{ config(
    materialized='materialized_view',
    indexes=[{'columns': ['user_id']}],
    on_configuration_change='apply'
) }}

select *
from {{ ref('events') }}
```

Supported values:

| Value | Behavior |
| --- | --- |
| `apply` | Apply index configuration changes. |
| `continue` | Keep going and emit a warning. |
| `fail` | Stop the run with an error. |

### Zero-Downtime Rebuilds

`materialized_view` and `view` support swap-based zero-downtime rebuilds.

```sql
{{ config(
    materialized='materialized_view',
    zero_downtime={'enabled': true}
) }}

select *
from {{ ref('events') }}
```

At runtime, enable the behavior with:

```bash
dbt run --vars 'zero_downtime: true'
```

For full details, see [zero-downtime-rebuilds.md](zero-downtime-rebuilds.md).

## Sink Configuration

The `sink` materialization supports two usage patterns.

### Adapter-Managed Sink DDL

Provide connector settings in model config and let the adapter build the `CREATE SINK` statement:

```sql
{{ config(
    materialized='sink',
    connector='kafka',
    connector_parameters={
      'topic': 'orders',
      'properties.bootstrap.server': '127.0.0.1:9092'
    },
    data_format='plain',
    data_encode='json',
    format_parameters={}
) }}

select *
from {{ ref('orders_mv') }}
```

Supported sink-specific configs:

| Key | Required | Description |
| --- | --- | --- |
| `connector` | Yes | Connector name placed in `WITH (...)`. |
| `connector_parameters` | Yes | Connector properties emitted into `WITH (...)`. |
| `data_format` | No | Sink format used in `FORMAT ...`. |
| `data_encode` | No | Sink encoding used in `ENCODE ...`. |
| `format_parameters` | No | Extra format/encode options emitted inside `FORMAT ... ENCODE ... (...)`. |

### Raw SQL Sink DDL

If `connector` is omitted, the adapter runs the SQL in the model as-is. This is useful when you want full control over the sink statement:

```sql
{{ config(materialized='sink') }}

create sink my_sink
from my_mv
with (
  connector = 'blackhole'
)
```

## Iceberg Documentation Sync

When `persist_docs` is enabled, `dbt-risingwave` can also sync model and column descriptions to the corresponding Iceberg table metadata via [PyIceberg](https://py.iceberg.apache.org/).

### Installation

PyIceberg is an optional dependency. Install it with the appropriate extra for your catalog's storage backend:

| Extra | Use case |
| --- | --- |
| `iceberg` | Base install — REST, Hive, SQL catalogs with local or generic storage |
| `iceberg-gcp` | Google Cloud (BigLake REST catalog, GCS storage, `google-auth`) |
| `iceberg-aws` | AWS (Glue, S3 storage via s3fs) |
| `iceberg-azure` | Azure (ADLS storage via adlfs) |

```shell
# Google Cloud / BigLake
pip install 'dbt-risingwave[iceberg-gcp]'

# AWS / Glue
pip install 'dbt-risingwave[iceberg-aws]'

# Azure
pip install 'dbt-risingwave[iceberg-azure]'

# Generic (REST, Hive, SQL catalogs without cloud-specific storage)
pip install 'dbt-risingwave[iceberg]'
```

### Profile Configuration

Add an `iceberg_catalog` block to your profile to define default catalog connection settings. These apply to all Iceberg-enabled models in that target:

```yaml
default:
  outputs:
    dev:
      type: risingwave
      host: 127.0.0.1
      user: root
      pass: ""
      dbname: dev
      port: 4566
      schema: public
      iceberg_catalog:
        type: rest
        uri: http://localhost:8181
  target: dev
```

Supported catalog types: `rest`, `hive`, `glue`, `dynamodb`, `sql`, `bigquery`. All keys in `iceberg_catalog` are passed directly to PyIceberg's `load_catalog()`, so any catalog-specific property (e.g. `warehouse`, `token`, `credential`) can be set here.

| Key | Description |
| --- | --- |
| `type` | Catalog type (e.g. `rest`, `glue`, `hive`). |
| `uri` | Catalog URI (REST endpoint or Thrift URI for Hive). |
| `warehouse` | Base storage location for tables (e.g. `s3://bucket/path`). |
| `name` | Logical name passed to `load_catalog()`. Defaults to `"default"`. |
| `iceberg_retry_attempts` | Total number of attempts for each catalog operation (connect, load table, commit). Defaults to `3`. Set to `1` to disable retries. |
| `iceberg_retry_delay` | Initial delay in seconds before the first retry. Doubles each attempt (exponential backoff). Defaults to `1.0`. |
| *(any other key)* | Forwarded to PyIceberg as a catalog property. |

### Opting In Per Model

Set `meta.iceberg: true` in the model config and provide the Iceberg table identity:

```yaml
# models/schema.yml
models:
  - name: my_iceberg_model
    description: "Sales fact table backed by Iceberg"
    config:
      meta:
        iceberg: true
        iceberg_namespace: prod_iceberg   # required
        iceberg_table: sales_facts        # required
    columns:
      - name: order_id
        description: "Unique order identifier"
```

Or inline in the model file:

```sql
{{ config(
    materialized='table',
    meta={
        'iceberg': true,
        'iceberg_namespace': 'prod_iceberg',
        'iceberg_table': 'sales_facts'
    }
) }}

select ...
```

| `meta` Key | Required | Description |
| --- | --- | --- |
| `iceberg` | Yes | Set to `true` to enable Iceberg doc sync for this model. |
| `iceberg_namespace` | Yes | The Iceberg namespace (database) containing the table. |
| `iceberg_table` | Yes | The Iceberg table name. |
| `iceberg_catalog` | No | Catalog config override for this model. Same keys as the profile block. Merged on top of the profile-level defaults, with model values winning. |

### Per-Model Catalog Override

A model can override the profile-level catalog config:

```yaml
config:
  meta:
    iceberg: true
    iceberg_namespace: prod
    iceberg_table: sales_facts
    iceberg_catalog:
      type: glue
      warehouse: s3://my-bucket/warehouse
```

Profile-level keys not present in the model override are still inherited.

### How It Works

When `persist_docs` runs for a model that has `meta.iceberg: true`:

1. The standard RisingWave `COMMENT ON TABLE` / `COMMENT ON COLUMN` SQL is applied first (existing behavior, always runs).
2. The adapter connects to the Iceberg catalog via PyIceberg.
3. The model description is written to the Iceberg table's `comment` property.
4. Column descriptions are written as field-level `doc` on the Iceberg schema.
5. Both changes are committed in a single atomic transaction.

The `persist_docs.relation` and `persist_docs.columns` flags are respected — setting `relation: false` skips the table-level comment, and `columns: false` skips column docs.

### Retry Behavior

Each catalog operation (connect, load table, commit) is retried up to `iceberg_retry_attempts` times with exponential backoff starting at `iceberg_retry_delay` seconds. For example, with the defaults (3 attempts, 1.0s delay):

- Attempt 1: immediate
- Attempt 2: after 1.0s
- Attempt 3: after 2.0s, then the final warning is logged

Retry warnings include the attempt number so you can see progress in the dbt log:

```
RisingWave adapter: Connecting to Iceberg catalog 'default' failed (attempt 1/3): ... Retrying in 1.0s...
```

Set `iceberg_retry_attempts: 1` to restore the previous immediate-fail behavior.

### Error Handling

Iceberg doc sync is best-effort. If all retry attempts fail (PyIceberg not installed, catalog unreachable, table not found), a warning is logged and the dbt run continues. The RisingWave SQL comments are not affected.

## Related dbt Configs

The adapter also works with standard dbt configs such as `indexes`, `contract`, `grants`, `unique_key`, and `on_schema_change`. Refer to the dbt docs for the generic semantics; this page focuses on RisingWave-specific behavior.
