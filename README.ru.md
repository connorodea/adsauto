# adsauto — модуль 1: товарный фид и Feed Doctor

Первый работающий кусок системы автоматизации Google-рекламы.
Забирает каталог из WooCommerce, приводит к модели Merchant Center,
собирает XML-фид и находит проблемы до того, как их найдёт Google.

Проверено на живых данных ESTACA (www.estaca.com.ua), 17.08.2026.

---

## Установка на Windows 11 с нуля

Ставится один раз, дальше всё живёт в контейнерах.

### 1. Docker Desktop

Скачать с https://www.docker.com/products/docker-desktop/, поставить,
перезагрузиться. При установке согласиться на WSL 2 — он подтянется сам.

Проверка в PowerShell:

```powershell
docker --version
docker compose version
```

### 2. Проект

```powershell
cd C:\adsauto
copy .env.example .env
notepad .env          # заменить change-me на нормальный пароль
docker compose up -d db
```

Postgres поднимется и сам применит `db/01_schema.sql`.

Проверка:

```powershell
docker compose exec db psql -U adsauto -d adsauto -c "\dt"
```

Должно вывести 11 таблиц.

### 3. Приложение

```powershell
docker compose build app
docker compose run --rm app python -m pytest
```

27 тестов должны пройти.

---

## Запуск

Собрать и проверить фид ESTACA:

```powershell
docker compose run --rm app python -m adsauto.cli feed `
  --site https://www.estaca.com.ua `
  --prefix estaca `
  --out feeds/estaca.xml `
  --json feeds/estaca-report.json
```

Фид появится в папке `feeds` на диске — её и отдаём Merchant Center
по scheduled fetch.

Без Docker (если стоит Python 3.12):

```powershell
pip install -r requirements.txt
$env:PYTHONPATH="src"
python -m adsauto.cli feed --site https://www.estaca.com.ua --prefix estaca
```

---

## Что уже сделано и почему именно так

### Store API вместо плагина фида

`/wp-json/wc/store/v1/` у ESTACA открыт публично — ключи не нужны.
Плагины фидов дают чёрный ящик, который нельзя ни проверить, ни
починить программно. Feed Doctor требует полного контроля над выгрузкой.

### Родитель вариативного товара в фид не попадает

У него `price_range`, а не цена. В фид идут только варианты,
объединённые через `item_group_id`.

### g:id строится от ID в CMS, а не от SKU

У всех трёх цветовых вариантов стола QUEENS `sku = "132-60"` — один и
тот же. Идентификатор из SKU схлопнул бы три товара в один. Проверено
тестом `test_offer_id_not_derived_from_sku`.

По той же причине родительский SKU не уходит в `mpn`: идентификатор,
одинаковый у трёх разных товаров, идентификатором не является.

### Цена делится на минорные единицы

WooCommerce отдаёт `{"price": "430000", "currency_minor_unit": 2}`.
Это 4 300 ₴. Прямое использование поля `price` дало бы стол за 430 000 ₴.

### Вариант наследует от родителя

Store API отдаёт у варианта пустые `categories`, `brands` и
`attributes`. Без наследования фид уехал бы без бренда и категории.

### Названия вариантов разворачиваются из слугов

В `variations[].attributes[].value` лежит `bilyj-bilyj`, а не
«Білий + Білий». Человекочитаемое название приходится доставать из
`attributes[].terms` родителя, иначе в заголовок товара попадает
транслит.

### identifier_exists=false

ESTACA — собственное производство, штрихкодов нет. Без явного
`identifier_exists=false` Google ждёт GTIN и отклоняет товар.

---

## Что нашлось в каталоге ESTACA

| Код | Уровень | Находка |
|---|---|---|
| F090 | warning | Заголовок «стіл 90×50 см» при атрибутах Ширина 1000 мм. Одно из двух неверно |
| F091 | info | SEO-хвост «\| ESTACA» в названии — бренд уже передаётся отдельным полем |
| F081 | info | Один SKU на три варианта — обработано, но знать об этом надо |

Ошибок уровня error на проверенной выборке нет — то есть после
обработки эти товары Merchant Center принял бы.

---

## Структура

```
db/01_schema.sql        схема Postgres (11 таблиц)
src/adsauto/
  models.py             нормализованная модель товара
  sources/woo.py        экстрактор WooCommerce Store API
  feed/build.py         генератор XML для Merchant Center
  feed/validate.py      валидатор — ядро Feed Doctor
  cli.py                командная строка
tests/
  fixtures/             реальные данные ESTACA от 17.08.2026
  test_pipeline.py      27 тестов
```

---

## Коды проверок

Уровни: `error` — Google отклонит; `warning` — примет, но показы
пострадают; `info` — на Google не влияет, влияет на нашу автоматику.

| Код | Уровень | Проверка |
|---|---|---|
| F001–F005 | error/warning | идентификатор, заголовок: пустой, длина, регистр, реклама в названии |
| F010–F012 | warning/error | описание: пустое, длина, остатки HTML |
| F020–F023 | error | цена: отсутствует, ноль, акция дороже обычной |
| F022 | warning | цена — выброс относительно медианы фида (признак потерянного деления) |
| F030 | error | недопустимое значение наличия |
| F040–F044 | error/info | ссылки и картинки: отсутствие, не https, лишние изображения |
| F050–F052 | error | бренд, GTIN, identifier_exists |
| F060–F061 | warning | категории Google и product_type |
| F070 | warning | вариант без различающего признака |
| F080–F084 | error/warning/info | по всему фиду: дубли id, общий SKU, дубли заголовков, консистентность групп |
| F090–F091 | warning/info | расхождение габаритов заголовка и атрибутов, SEO-хвост |

Все проверки детерминированные. Ни одна не вызывает языковую модель:
длину строки и уникальность идентификатора должен считать код.
ИИ подключается на следующем этапе — там, где нужен смысл.

---

## Дальше

1. Складывать товары и находки валидатора в Postgres (таблицы готовы).
2. Забирать из Merchant Center реальные статусы отклонений и сверять
   с предсказаниями валидатора — так проверяется он сам.
3. Автопочинка находок, которые чинятся однозначно.
4. Сбор семантики из Keyword Planner API — после получения
   developer token.

Схема БД уже содержит таблицы под ядро, черновики кампаний и вердикты
QA: `keywords.raw_response_id` объявлен `NOT NULL`, то есть ключевое
слово физически нельзя записать без ссылки на сохранённый сырой ответ
API. Придуманных моделью ключей в базе не будет.
