import os

class Config:
    """基礎配置類"""
    # 資料庫配置
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_DATABASE = os.environ.get('DB_DATABASE', 'user')
    
    # 上傳配置
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', './uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    
    # 前端路徑配置
    FRONTEND_TEMPLATES = os.environ.get('FRONTEND_TEMPLATES', '../frontend/templates')
    ADMIN_TEMPLATES = os.environ.get('ADMIN_TEMPLATES', '../admin_frontend/templates')
    
    # API 配置
    API_PREFIX = os.environ.get('API_PREFIX', '/api')
    
    # 安全配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-here')
    
    # CORS 配置
    CORS_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:5000", 
        "http://127.0.0.1:5000",
        "http://localhost:8080"
    ]

class DevelopmentConfig(Config):
    """開發環境配置"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """生產環境配置"""
    DEBUG = False
    TESTING = False
    
    # 生產環境的 CORS 設定
    CORS_ORIGINS = [
        "https://yourdomain.com",
        "https://admin.yourdomain.com"
    ]

class TestingConfig(Config):
    """測試環境配置"""
    DEBUG = True
    TESTING = True
    DB_DATABASE = 'test_user'

# 配置映射
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
} 