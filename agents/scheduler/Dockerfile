FROM alpine:3.19

RUN apk add --no-cache docker-cli docker-cli-compose

WORKDIR /app

COPY agents/scheduler/crontab /etc/crontabs/root
COPY agents/scheduler/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
