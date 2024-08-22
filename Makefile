.PHONY: config
config:
	rm -rf clickhouse01 clickhouse02 clickhouse03
	mkdir -p clickhouse01 clickhouse02 clickhouse03
	REPLICA=01 SHARD=01 envsubst < config.xml > clickhouse01/config.xml
	REPLICA=02 SHARD=01 envsubst < config.xml > clickhouse02/config.xml
	REPLICA=03 SHARD=01 envsubst < config.xml > clickhouse03/config.xml
	cp users.xml clickhouse01/users.xml
	cp users.xml clickhouse02/users.xml
	cp users.xml clickhouse03/users.xml


.PHONY: prepare
prepare:
	/opt/homebrew/bin/colima start --cpu 4 --memory 8
	docker build -t clickhouse_python_dbt .

.PHONY: up
up: prepare
	docker-compose up -d

.PHONY: start
start:
start: config up
	docker-compose start

.PHONY: stop
stop:
	docker-compose stop

.PHONY: down
down: stop
	docker-compose down
