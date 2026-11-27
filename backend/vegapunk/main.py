from vegapunk.app import create_app
from vegapunk.config import load_settings

app = create_app(load_settings())
