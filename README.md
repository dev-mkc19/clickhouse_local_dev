# Clickhouse Cluster

Clickhouse cluster with 1 shards and 3 replicas built with docker-compose. Includes custom dbt-clickhouse adapter.
It has aim to easely set ap sandbox for developing DBT project.

> [!warn]
> Not for production use.

## Configuration

1. Add credentials to `.env` for approptiate ENV VARS
    - **CLICKHOUSE_SCR_HOST** - credentials for source Clickhouse cluster which will be used as template fow sandbox.
    - **CLICKHOUSE_SCR_PORT** - credentials for source Clickhouse cluster which will be used as template fow sandbox.
    - **CLICKHOUSE_SCR_USER** - credentials for source Clickhouse cluster which will be used as template fow sandbox.
    - **CLICKHOUSE_SCR_PASSWORD** - credentials for source Clickhouse cluster which will be used as template fow sandbox.
    - **DBT_USER** - user wich will be used in profiles.yaml
    - **DBT_PASSWORD** - user wich will be used in profiles.yaml
    - **MOUNT_DBT_PROJECT_DIR** - an absolute path to local DBT project
2. Switch on tech VPN


## Run

Run single command, and it will copy configs for each node and
run empty clickhouse cluster `cluster_name` with docker-compose
```sh
make start
```

Containers will be available in docker network `172.23.0.0/24`

| Container    | Address
| ------------ | -------
| zookeeper    | 172.23.0.10
| clickhouse01 | 172.23.0.11
| clickhouse02 | 172.23.0.12
| clickhouse03 | 172.23.0.13


Run single command, and it will copy migration.sh script inside a docker compose and run it.
After that script will re-create database structure as it was in Clickhouse source system with the sample data
of each source table in DBT project (~100 rows)
```sh
make migrate
```

> [!info]
> You can rewrite executing DBT_SOURCES var in `migration.sh` script by adding `--select <add selection>` attribute
> and it will reduce a number of source tables used for re-creation.


Now you are ready to use sandbox. Return to your local BDT project an run `dbt run-operation create_udfs -t dev`
it will create nessessary function. Done. Enjoy you dev process.


## Profiles

- `default` - no password
- `admin` - password `123`
- `airflow` - password

## Test it

Login to clickhouse01 console (first node's ports are mapped to localhost)
```sh
clickhouse-client -h localhost
```

Or open `clickhouse-client` inside any container
```sh
docker exec -it clickhouse01 clickhouse-client -h localhost
```

Create a test database and table (sharded and replicated)
```sql
CREATE DATABASE company_db ON CLUSTER 'cluster_name';

CREATE TABLE company_db.events ON CLUSTER 'cluster_name' (
    time DateTime,
    uid  Int64,
    type LowCardinality(String)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{cluster}/{shard}/events', '{replica}')
PARTITION BY toDate(time)
ORDER BY (uid);

CREATE TABLE company_db.events_distr ON CLUSTER 'cluster_name' AS company_db.events
ENGINE = Distributed('cluster_name', company_db, events, uid);
```

Load some data
```sql
INSERT INTO company_db.events_distr VALUES
    ('2020-01-01 10:00:00', 100, 'view'),
    ('2020-01-01 10:05:00', 101, 'view'),
    ('2020-01-01 11:00:00', 100, 'contact'),
    ('2020-01-01 12:10:00', 101, 'view'),
    ('2020-01-02 08:10:00', 100, 'view'),
    ('2020-01-03 13:00:00', 103, 'view');
```

Check data from the current shard
```sql
SELECT * FROM company_db.events;
```

Check data from all cluster
```sql
SELECT * FROM company_db.events_distr;
```

## Add more nodes

If you need more Clickhouse nodes, add them like this:

1. Add replicas/shards to `config.xml` to the block `company/remote_servers/cluster_name`.
1. Add nodes to `docker-compose.yml`.
1. Add nodes in `Makefile` in `config` target.

## Start, stop

Start/stop the cluster without removing containers
```sh
make start
make stop
```

## Teardown

Stop and remove containers
```sh
make down
```
