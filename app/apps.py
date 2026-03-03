from django.apps import AppConfig


class AppConfig(AppConfig):
    name = 'app'


    def ready(self):
        # Warm the pipeline cache when Django starts
        # Runs in a background thread so startup is not delayed
        import threading
        from app.services.pipeline import _refresh_in_background
        threading.Timer(2.0, _refresh_in_background).start()