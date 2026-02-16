import logging

logger = logging.getLogger(__name__)


class WarehouseRouter:
    """
    Database router for read-only MS SQL Server - NPHI data warehouse.

    Guarantees:
    - ALL warehouse reads go to MSSQL
    - NO migrations on MSSQL
    - Hard block on accidental writes
    """

    WAREHOUSE_APPS = {"warehouse"}

    def db_for_read(self, model, **hints):
        """
        Send warehouse reads to MSSQL.
        """
        if model._meta.app_label in self.WAREHOUSE_APPS:
            return "warehouse"
        return "default"

    def db_for_write(self, model, **hints):
        """
        Block writes to warehouse DB.
        """
        if model._meta.app_label in self.WAREHOUSE_APPS:
            logger.critical(
                "WRITE ATTEMPT BLOCKED on warehouse DB: %s.%s",
                model._meta.app_label,
                model.__name__,
            )
            return None  # HARD BLOCK
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        """
        Prevent cross-database relations.
        """
        if (
            obj1._meta.app_label in self.WAREHOUSE_APPS
            or obj2._meta.app_label in self.WAREHOUSE_APPS
        ):
            return False
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Never migrate warehouse models.
        """
        if app_label in self.WAREHOUSE_APPS:
            return False
        return db == "default"
