.PHONY: config
config:
	rm -rf clickhouse01 clickhouse02 clickhouse03
	mkdir -p clickhouse01/config.d clickhouse02/config.d clickhouse03/config.d
	mkdir -p clickhouse01/users.d clickhouse02/users.d clickhouse03/users.d
	REPLICA=01 SHARD=01 envsubst < config.yaml > clickhouse01/config.d/config.yaml
	REPLICA=02 SHARD=01 envsubst < config.yaml > clickhouse02/config.d/config.yaml
	REPLICA=03 SHARD=01 envsubst < config.yaml > clickhouse03/config.d/config.yaml
	cp kafka/kafka.yaml clickhouse01/config.d/kafka.yaml
	cp kafka/kafka.yaml clickhouse02/config.d/kafka.yaml
	cp kafka/kafka.yaml clickhouse03/config.d/kafka.yaml
	DBT_USER=${DBT_USER} DBT_PASSWORD=${DBT_PASSWORD} envsubst < users.yaml > clickhouse01/users.d/users.yaml
	DBT_USER=${DBT_USER} DBT_PASSWORD=${DBT_PASSWORD} envsubst < users.yaml > clickhouse02/users.d/users.yaml
	DBT_USER=${DBT_USER} DBT_PASSWORD=${DBT_PASSWORD} envsubst < users.yaml > clickhouse03/users.d/users.yaml
	# cp users.yaml clickhouse01/users.d/users.yaml
	# cp users.yaml clickhouse02/users.d/users.yaml
	# cp users.yaml clickhouse03/users.d/users.yaml


.PHONY: prepare
prepare:
	/opt/homebrew/bin/colima start --cpu 4 --memory 8
	docker build -t clickhouse_python_dbt .

.PHONY: up
up: prepare
	docker-compose up -d

.PHONY: migrate
migrate:
	docker cp ./migration.sh clickhouse01:/opt/dbt
	docker exec clickhouse01 chmod +x /opt/dbt/migration.sh
	docker exec clickhouse01 sh /opt/dbt/migration.sh

.PHONY: start 
start: config up
	docker-compose start

.PHONY: kafka-up
kafka-up:
	docker-compose -f docker-compose.kafka.yml up -d

.PHONY: kafka-init
kafka-init:
	until docker exec clickhouse01 clickhouse-client --query "SELECT 1" >/dev/null 2>&1; do sleep 2; done

.PHONY: kafka-start
kafka-start: kafka-up kafka-init

.PHONY: kafka-stop
kafka-stop:
	docker-compose -f docker-compose.kafka.yml stop

.PHONY: stop
stop:
	docker-compose stop

.PHONY: down
down: stop
	docker-compose down
	docker-compose -f docker-compose.kafka.yml down
	rm -rf clickhouse01 clickhouse02 clickhouse03
