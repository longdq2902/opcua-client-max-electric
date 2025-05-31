import os
from pathlib import Path

# Thư mục gốc của dự án là thư mục hiện tại
project_root = Path(".") # "." đại diện cho thư mục hiện tại

# Danh sách các thư mục cần tạo (đường dẫn tương đối so với project_root)
directories = [
    "app",
    "app/static",
    "app/static/css",
    "app/static/js",
    "app/templates",
    "app/templates/servers",
    "app/templates/nodes",
]

# Danh sách các file rỗng cần tạo (đường dẫn tương đối so với project_root)
empty_files = [
    "app/static/css/style.css",
    "app/static/js/script.js",
    "app/templates/base.html",
    "app/templates/index.html",
    "app/templates/servers/list.html",
    "app/templates/servers/form.html",
    "app/templates/nodes/tree.html",
    "app/templates/nodes/detail.html",
    "app/__init__.py",
    "app/routes.py",
    "app/models.py",
    "app/opcua_client.py",
    "app/config.py",
    "run.py",
    "requirements.txt", # Tạo file rỗng, sẽ thêm nội dung sau
]

print(f"Tạo cấu trúc dự án trong thư mục hiện tại: {project_root.resolve()}")

# Tạo các thư mục
for directory in directories:
    dir_path = project_root / directory
    dir_path.mkdir(parents=True, exist_ok=True)
    print(f"Đã tạo thư mục: {dir_path.resolve()}")

# Tạo các file rỗng
for file_path_str in empty_files:
    file_path = project_root / file_path_str
    file_path.touch(exist_ok=True)
    print(f"Đã tạo file: {file_path.resolve()}")

print("Hoàn tất tạo cấu trúc dự án!")