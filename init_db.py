# init_database.py
from app import create_app, db

# Tạo một instance của ứng dụng Flask
# Điều này cần thiết để có application context cho SQLAlchemy
app = create_app()

# Đẩy application context
# Tất cả các thao tác với database cần được thực hiện trong một application context
with app.app_context():
    print("Đang chuẩn bị tạo các bảng database...")
    
    # Lệnh này sẽ tạo tất cả các bảng được định nghĩa trong models.py
    # mà chưa tồn tại trong database.
    db.create_all() 
    
    print("Hoàn tất việc tạo bảng database!")
    print(f"File database nên được tạo tại: {app.config['SQLALCHEMY_DATABASE_URI']}")

if __name__ == '__main__':
    # Bạn có thể thêm các logic khác ở đây nếu muốn,
    # ví dụ như thêm một vài dữ liệu mẫu ban đầu.
    pass