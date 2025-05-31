# app/config.py
import os

# Lấy đường dẫn thư mục gốc của ứng dụng
basedir = os.path.abspath(os.path.dirname(__file__)) # __file__ là app/config.py, dirname(__file__) là app/

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'MaxElectric@2025' # Rất quan trọng cho production
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'opcua_app.db') # Đường dẫn tới file DB SQLite
    SQLALCHEMY_TRACK_MODIFICATIONS = False