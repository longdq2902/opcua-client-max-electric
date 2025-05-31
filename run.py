# run.py
from app import create_app, db # Import create_app và db từ package app
import logging


# Tạo instance của ứng dụng sử dụng Application Factory
app = create_app()



if __name__ == '__main__':
    # Chạy ứng dụng Flask ở chế độ debug
    # Chế độ debug sẽ tự động tải lại server khi có thay đổi code
    # và hiển thị thông báo lỗi chi tiết hơn.
    # KHÔNG BAO GIỜ chạy ở chế độ debug trong môi trường production.
    app.run(debug=True)