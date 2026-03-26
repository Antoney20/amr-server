# import logging

# logger = logging.getLogger(__name__)


# class WarehouseRouter:
#     """
#     Database router for read-only MS SQL Server - NPHI data warehouse.

#     Guarantees:
#     - ALL warehouse reads go to MSSQL
#     - NO migrations on MSSQL
#     - Hard block on accidental writes
#     """

#     WAREHOUSE_APPS = {"warehouse"}

#     def db_for_read(self, model, **hints):
#         """
#         Send warehouse reads to MSSQL.
#         """
#         if model._meta.app_label in self.WAREHOUSE_APPS:
#             return "warehouse"
#         return "default"

#     def db_for_write(self, model, **hints):
#         if model._meta.app_label in self.WAREHOUSE_APPS:
#             logger.critical(
#                 "WRITE ATTEMPT BLOCKED on warehouse DB: %s.%s",
#                 model._meta.app_label,
#                 model.__name__,
#             )
#             return None
#         return None

#     def allow_relation(self, obj1, obj2, **hints):
#         """
#         Prevent cross-database relations.
#         """
#         if (
#             obj1._meta.app_label in self.WAREHOUSE_APPS
#             or obj2._meta.app_label in self.WAREHOUSE_APPS
#         ):
#             return False
#         return True

#     def allow_migrate(self, db, app_label, model_name=None, **hints):
#         """
#         Never migrate warehouse models.
#         """
#         if app_label in self.WAREHOUSE_APPS:
#             return False
#         return db == "default"



"""
core/db_router.py — Read-only warehouse router.

Rules:
  - Warehouse app label → warehouse DB for reads
  - Warehouse app label → hard-block writes (return None, not "default")
  - Everything else   → return None (Django picks "default" naturally)
  - No migrations on warehouse DB ever
"""

import logging

logger = logging.getLogger(__name__)


class WarehouseRouter:
    """
    Routes warehouse-labelled models to the MSSQL read replica.

    Returning None from any method means "I have no opinion —
    keep asking other routers / use Django's default."  This is
    critical: returning "default" from db_for_write() would make
    the router actively intercept ALL model writes, which broke
    management commands like createsuperuser.
    """

    WAREHOUSE_APPS = frozenset({"warehouse"})

    # ------------------------------------------------------------------
    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.WAREHOUSE_APPS:
            return "warehouse"
        return None  # not our concern

    # ------------------------------------------------------------------
    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.WAREHOUSE_APPS:
            logger.critical(
                "WRITE ATTEMPT BLOCKED on warehouse DB: %s.%s",
                model._meta.app_label,
                model.__name__,
            )
            return None  # None = block (no DB accepts it) for warehouse models
        return None  # not our concern — Django routes to "default" on its own

    # ------------------------------------------------------------------
    def allow_relation(self, obj1, obj2, **hints):
        # Prevent cross-database FK relations
        db1 = obj1._meta.app_label in self.WAREHOUSE_APPS
        db2 = obj2._meta.app_label in self.WAREHOUSE_APPS
        if db1 != db2:
            return False        # one warehouse, one not → deny
        if db1 and db2:
            return True         # both warehouse → allow
        return None             # both non-warehouse → not our concern

    # ------------------------------------------------------------------
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.WAREHOUSE_APPS:
            return False        # never migrate warehouse models
        if db == "warehouse":
            return False        # never run any migration on warehouse DB
        return None             # not our concern