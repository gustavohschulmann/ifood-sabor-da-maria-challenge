from decimal import Decimal

from pydantic import BaseModel, computed_field


class Quantity(BaseModel):
    value: Decimal
    unit: str


class PackageInfo(BaseModel):
    """
    Represents units such as:
      un 500g
      un 400g
      un 500ml
      balde 2kg

    Example:
      container = "un"
      content_quantity = 500
      content_unit = "g"
    """

    container: str
    content_quantity: Decimal
    content_unit: str


class PurchaseInfo(BaseModel):
    quantity: Quantity
    total_paid: Decimal
    package: PackageInfo | None = None

    @computed_field
    @property
    def cost_per_purchase_unit(self) -> Decimal:
        """
        Examples:

        2 kg purchased for R$28
        => R$14/kg

        30 units purchased for R$24
        => R$0.80/unit

        1 package purchased for R$23.67
        => R$23.67/package
        """
        return self.total_paid / self.quantity.value


class PantryItem(BaseModel):
    ingredient: str
    stock: Quantity

    # None means we don't know its historical cost.
    purchase: PurchaseInfo | None = None

    # Only populated for package-style stock such as "un 500g".
    package: PackageInfo | None = None


class Pantry(BaseModel):
    items: list[PantryItem]
    warnings: list[str] = []

    def get(self, ingredient: str) -> PantryItem | None:
        return next(
            (
                item
                for item in self.items
                if item.ingredient == ingredient
            ),
            None,
        )