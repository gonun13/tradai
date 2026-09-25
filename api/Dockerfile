FROM composer:2.7 AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --optimize-autoloader --no-interaction --no-scripts

FROM php:8.3.20-cli-alpine
WORKDIR /app
RUN apk add --no-cache sqlite-libs sqlite-dev $PHPIZE_DEPS \
  && docker-php-ext-install pdo pdo_sqlite \
  && apk del $PHPIZE_DEPS sqlite-dev
COPY --from=vendor /app/vendor ./vendor
COPY composer.json ./
COPY public ./public
COPY src ./src
COPY test_holding_daily_change.php test_currency.php test_history.php ./
ENV TRADAI_DATA_DIR=/data
EXPOSE 8080
CMD ["php", "-S", "0.0.0.0:8080", "-t", "public", "public/index.php"]
