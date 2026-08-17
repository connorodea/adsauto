"""Валидатор фида — ядро Feed Doctor.

Все проверки здесь ДЕТЕРМИНИРОВАННЫЕ. Ни одна не обращается к языковой
модели. Это принципиально: длину строки, совпадение цены и уникальность
идентификатора должен считать код, а не модель, которая считает символы
плохо и делает это каждый раз по-новому.

ИИ подключается на следующем этапе и только там, где нужен смысл:
достоверность утверждений о товаре, грамматика, соответствие интенту.

Уровни:
  error   — Google отклонит товар
  warning — Google примет, но показы пострадают
  info    — на качество не влияет, влияет на нашу дальнейшую автоматику
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable, Literal, Sequence

from ..models import Product

Severity = Literal["error", "warning", "info"]

TITLE_MAX = 150
DESCRIPTION_MAX = 5000
PRODUCT_TYPE_MAX = 750
ALLOWED_AVAILABILITY = {"in_stock", "out_of_stock", "backorder", "preorder"}
PROMO_MARKERS = ("!!!", "№1", "n°1", "самый дешевый", "найдешевший", "бесплатно!!!")


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    offer_id: str
    message: str
    fix: str | None = None

    def __str__(self) -> str:
        return f"[{self.severity.upper()} {self.code}] {self.offer_id}: {self.message}"


# --- проверки одного товара -------------------------------------------


def _check_offer_id(p: Product) -> Iterable[Issue]:
    if not p.offer_id:
        yield Issue("F001", "error", "<нет id>", "Отсутствует g:id",
                    "Идентификатор обязателен и должен быть стабильным")


def _check_title(p: Product) -> Iterable[Issue]:
    if not p.title.strip():
        yield Issue("F002", "error", p.offer_id, "Пустой заголовок",
                    "Заполнить название товара в CMS")
        return
    if len(p.title) > TITLE_MAX:
        yield Issue("F003", "error", p.offer_id,
                    f"Заголовок {len(p.title)} символов при лимите {TITLE_MAX}",
                    "Сократить название или убрать суффикс бренда")
    if p.title.isupper():
        yield Issue("F004", "warning", p.offer_id, "Заголовок целиком в верхнем регистре",
                    "Привести к обычному регистру")
    low = p.title.lower()
    for marker in PROMO_MARKERS:
        if marker in low:
            yield Issue("F005", "error", p.offer_id,
                        f"Рекламный текст в заголовке: {marker!r}",
                        "Заголовок должен описывать товар, а не рекламировать его")


def _check_description(p: Product) -> Iterable[Issue]:
    if not p.description.strip():
        yield Issue("F010", "warning", p.offer_id, "Пустое описание",
                    "Описание влияет на релевантность показов")
    elif len(p.description) > DESCRIPTION_MAX:
        yield Issue("F011", "error", p.offer_id,
                    f"Описание {len(p.description)} символов при лимите {DESCRIPTION_MAX}",
                    "Обрезать по границе предложения")
    if "<" in p.description and ">" in p.description:
        yield Issue("F012", "error", p.offer_id, "В описании остался HTML",
                    "Очистить теги перед выгрузкой")


def _check_price(p: Product) -> Iterable[Issue]:
    if p.price is None:
        yield Issue("F020", "error", p.offer_id, "Нет цены",
                    "Товар без цены Merchant Center не принимает")
        return
    if p.price <= 0:
        yield Issue("F021", "error", p.offer_id, f"Некорректная цена: {p.price}",
                    "Проверить выгрузку цены из CMS")
    if p.sale_price is not None and p.price is not None and p.sale_price > p.price:
        yield Issue("F023", "error", p.offer_id,
                    f"Цена по акции {p.sale_price} выше обычной {p.price}",
                    "Поменять местами или убрать акцию")


def _check_availability(p: Product) -> Iterable[Issue]:
    if p.availability not in ALLOWED_AVAILABILITY:
        yield Issue("F030", "error", p.offer_id,
                    f"Недопустимое значение наличия: {p.availability!r}",
                    f"Допустимы: {', '.join(sorted(ALLOWED_AVAILABILITY))}")


def _check_links(p: Product) -> Iterable[Issue]:
    if not p.link:
        yield Issue("F040", "error", p.offer_id, "Нет ссылки на товар", None)
    elif not p.link.startswith("https://"):
        yield Issue("F041", "error", p.offer_id, f"Ссылка не по https: {p.link}",
                    "Merchant Center требует https")

    if not p.image_link:
        yield Issue("F042", "error", p.offer_id, "Нет главной картинки",
                    "Товар без изображения не попадёт в Shopping")
    elif not p.image_link.startswith("https://"):
        yield Issue("F043", "error", p.offer_id, "Картинка не по https", None)

    if len(p.additional_image_links) > 10:
        yield Issue("F044", "info", p.offer_id,
                    f"Дополнительных картинок {len(p.additional_image_links)}, Google берёт 10",
                    "Лишние будут отброшены")


def _check_identifiers(p: Product) -> Iterable[Issue]:
    """Собственное производство без GTIN — законный случай.

    Но тогда обязана стоять связка identifier_exists=false, иначе Google
    ждёт GTIN и отклоняет товар.
    """
    has_gtin = bool(p.gtin)
    has_mpn = bool(p.mpn)
    if not p.brand:
        yield Issue("F050", "error", p.offer_id, "Не указан бренд",
                    "Для товаров собственного производства — название компании")
    if not has_gtin and not (has_mpn and p.brand):
        if p.identifier_exists:
            yield Issue("F051", "error", p.offer_id,
                        "Нет GTIN и нет пары бренд+MPN, но identifier_exists не выставлен в false",
                        "Проставить identifier_exists=false")
    if has_gtin and not p.gtin.isdigit():
        yield Issue("F052", "error", p.offer_id, f"GTIN не числовой: {p.gtin!r}", None)


def _check_categories(p: Product) -> Iterable[Issue]:
    if not p.google_product_category:
        yield Issue("F060", "warning", p.offer_id, "Нет google_product_category",
                    "Без категории Google угадывает сам, точность падает")
    if p.product_type and len(p.product_type) > PRODUCT_TYPE_MAX:
        yield Issue("F061", "warning", p.offer_id, "Слишком длинный product_type", None)


def _check_variant(p: Product) -> Iterable[Issue]:
    if p.is_variant and not p.variant_label:
        yield Issue("F070", "warning", p.offer_id,
                    "Вариант без различающего признака в заголовке",
                    "Варианты с одинаковыми заголовками Google считает дублями")


DIM_KEYS = {
    "ширина": 0, "глибина": 1, "довжина": 1, "висота": 2,
    "глубина": 1, "длина": 1, "высота": 2,
}
_DIM_IN_TITLE = re.compile(
    r"(\d{2,4})\s*[×xхX*]\s*(\d{2,4})(?:\s*[×xхX*]\s*(\d{2,4}))?\s*(мм|см|cm|mm)?",
    re.IGNORECASE,
)


def _declared_dimensions(p: Product) -> dict[int, int]:
    """Габариты из атрибутов товара, приведённые к миллиметрам."""
    out: dict[int, int] = {}
    for name, value in p.product_details:
        low = name.lower()
        for key, axis in DIM_KEYS.items():
            if not low.startswith(key):
                continue
            digits = re.sub(r"\D", "", value)
            if not digits:
                continue
            mm = int(digits)
            if "см" in low or "cm" in low:
                mm *= 10
            out[axis] = mm
            break
    return out


def _check_dimension_mismatch(p: Product) -> Iterable[Issue]:
    """Габариты в заголовке не совпадают с атрибутами товара.

    Найдено на реальных данных ESTACA: заголовок «стіл 90×50 см»
    при атрибутах Ширина 1000 мм, Глибина 500 мм. Одно из двух неверно.
    Google сверяет заголовок с посадочной страницей, а покупатель —
    с тем, что приехало.
    """
    declared = _declared_dimensions(p)
    if not declared:
        return
    match = _DIM_IN_TITLE.search(p.title)
    if not match:
        return

    unit = (match.group(4) or "").lower()
    scale = 10 if unit in ("см", "cm") else 1
    if not unit:
        # без единицы: трёхзначные и больше считаем миллиметрами
        scale = 1 if len(match.group(1)) >= 3 else 10

    from_title = {
        axis: int(match.group(axis + 1)) * scale
        for axis in range(3)
        if match.group(axis + 1)
    }
    for axis, value in from_title.items():
        expected = declared.get(axis)
        if expected and abs(expected - value) > max(5, expected * 0.02):
            axis_name = ("ширина", "глубина", "высота")[axis]
            yield Issue("F090", "warning", p.offer_id,
                        f"В заголовке {axis_name} {value} мм, в атрибутах {expected} мм",
                        "Привести название и характеристики к одному значению")
            return


def _check_title_noise(p: Product) -> Iterable[Issue]:
    """Хвост вида «| BRAND» — сеошный мусор, съедающий лимит заголовка.

    Ищем не только в конце: у вариантов к названию дописывается признак
    варианта, и хвост оказывается в середине.
    """
    if p.brand and f"| {p.brand}" in p.title:
        yield Issue("F091", "info", p.offer_id,
                    f"В заголовке SEO-хвост «| {p.brand}»",
                    "Бренд уже передаётся отдельным полем g:brand; "
                    "в заголовке он тратит лимит и мешает варианту")
    elif re.search(r"\s\|\s\S+\s*$", p.title):
        yield Issue("F091", "info", p.offer_id,
                    "В заголовке SEO-хвост после «|»",
                    "Для фида хвост лучше убирать: он не помогает и тратит лимит")


SINGLE_CHECKS: Sequence[Callable[[Product], Iterable[Issue]]] = (
    _check_offer_id,
    _check_title,
    _check_description,
    _check_price,
    _check_availability,
    _check_links,
    _check_identifiers,
    _check_categories,
    _check_variant,
    _check_dimension_mismatch,
    _check_title_noise,
)


# --- проверки по всему фиду -------------------------------------------


def _check_duplicate_ids(products: Sequence[Product]) -> Iterable[Issue]:
    for offer_id, count in Counter(p.offer_id for p in products).items():
        if count > 1:
            yield Issue("F080", "error", offer_id,
                        f"Идентификатор встречается {count} раз",
                        "g:id обязан быть уникальным в пределах фида")


def _check_shared_sku(products: Sequence[Product]) -> Iterable[Issue]:
    """Классика WooCommerce: у всех вариантов один sku родителя.

    Само по себе Google не ломает — ломает попытка использовать такой sku
    как идентификатор. Фиксируем, чтобы это не стало сюрпризом.
    """
    by_sku: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if p.sku:
            by_sku[p.sku].append(p)
    for sku, group in by_sku.items():
        if len(group) > 1:
            yield Issue("F081", "info", group[0].offer_id,
                        f"SKU {sku!r} общий для {len(group)} товаров",
                        "В качестве g:id используется ID из CMS, это ожидаемо")


def _check_duplicate_titles(products: Sequence[Product]) -> Iterable[Issue]:
    by_title: dict[tuple[str, str | None], list[Product]] = defaultdict(list)
    for p in products:
        by_title[(p.title.strip().lower(), p.item_group_id)].append(p)
    for (title, _), group in by_title.items():
        if len(group) > 1 and title:
            yield Issue("F082", "warning", group[0].offer_id,
                        f"Заголовок повторяется у {len(group)} товаров одной группы",
                        "Добавить различающий признак варианта")


def _check_group_consistency(products: Sequence[Product]) -> Iterable[Issue]:
    groups: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if p.item_group_id:
            groups[p.item_group_id].append(p)
    for gid, group in groups.items():
        brands = {p.brand for p in group}
        if len(brands) > 1:
            yield Issue("F083", "warning", gid,
                        f"В группе {gid} разные бренды: {brands}", None)
        if len({p.image_link for p in group}) == 1 and len(group) > 1:
            yield Issue("F084", "info", gid,
                        f"Все {len(group)} вариантов группы используют одну картинку",
                        "У цветовых вариантов картинки должны различаться")


def _check_price_outliers(products: Sequence[Product]) -> Iterable[Issue]:
    """Цена, не поделённая на минорные единицы, — самая дорогая ошибка фида.

    Абсолютный порог тут не работает: 430 000 ₴ подозрительны для мебели
    и нормальны для станка. Поэтому сравниваем с медианой самого фида —
    товар дороже медианы в OUTLIER_FACTOR раз почти всегда означает,
    что где-то потерялось деление на 100.
    """
    OUTLIER_FACTOR = Decimal(50)

    priced = sorted((p.price for p in products if p.price and p.price > 0))
    if len(priced) < 3:
        return
    median = priced[len(priced) // 2]
    if median <= 0:
        return
    for p in products:
        if p.price and p.price / median >= OUTLIER_FACTOR:
            yield Issue("F022", "warning", p.offer_id,
                        f"Цена {p.price} выше медианной по фиду ({median}) "
                        f"в {int(p.price / median)} раз",
                        "Проверить деление на currency_minor_unit")


FEED_CHECKS = (
    _check_duplicate_ids,
    _check_shared_sku,
    _check_duplicate_titles,
    _check_group_consistency,
    _check_price_outliers,
)


# --- точка входа ------------------------------------------------------


@dataclass
class Report:
    issues: list[Issue]
    total: int

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def blocked_offer_ids(self) -> set[str]:
        return {i.offer_id for i in self.errors}

    @property
    def rejection_rate(self) -> float:
        if not self.total:
            return 0.0
        return round(100 * len(self.blocked_offer_ids) / self.total, 1)

    def by_code(self) -> dict[str, int]:
        return dict(Counter(i.code for i in self.issues).most_common())

    def summary(self) -> str:
        lines = [
            f"Товаров в фиде: {self.total}",
            f"Будет отклонено Google: {len(self.blocked_offer_ids)} ({self.rejection_rate}%)",
            f"Ошибок: {len(self.errors)}, предупреждений: {len(self.warnings)}",
            "",
            "По кодам:",
        ]
        lines += [f"  {code}: {count}" for code, count in self.by_code().items()]
        return "\n".join(lines)


def validate(products: Sequence[Product]) -> Report:
    issues: list[Issue] = []
    for product in products:
        for check in SINGLE_CHECKS:
            issues.extend(check(product))
    for feed_check in FEED_CHECKS:
        issues.extend(feed_check(products))
    return Report(issues=issues, total=len(products))
