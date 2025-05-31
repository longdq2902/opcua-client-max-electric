# app/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, URL, Optional, Regexp # Optional cho các trường không bắt buộc

class OpcServerForm(FlaskForm):
    name = StringField('Tên Server', 
                       validators=[DataRequired(message="Tên server không được để trống."), 
                                   Length(min=2, max=100, message="Tên server phải từ 2 đến 100 ký tự.")])

    endpoint_url = StringField('Endpoint URL',
                               validators=[DataRequired(message="Endpoint URL không được để trống."),
                                           Regexp(r'^opc\.tcp://[a-zA-Z0-9.-]+(:\d+)?(/.*)?$', # Regex cơ bản
                                                  message="Endpoint URL không hợp lệ. Phải có dạng opc.tcp://hostname:port[/path_optional]"),
                                           Length(max=255)])

    description = TextAreaField('Mô tả', 
                                validators=[Optional(), Length(max=500)])

    # --- Cấu hình Bảo mật ---
    security_mode = SelectField('Security Mode', 
                                choices=[
                                    ('', '-- Không chọn --'),
                                    ('None', 'None'),
                                    ('Sign', 'Sign'),
                                    ('SignAndEncrypt', 'SignAndEncrypt')
                                ],
                                validators=[Optional()])

    security_policy_uri = StringField('Security Policy URI',
                                      validators=[Optional(), Length(max=255)])

    # --- Cấu hình Xác thực Người dùng ---
    user_auth_type = SelectField('Loại Xác thực',
                                 choices=[
                                     ('Anonymous', 'Anonymous'),
                                     ('Username', 'Username/Password')
                                     # ('Certificate', 'Certificate') # Có thể thêm sau
                                 ],
                                 default='Anonymous',
                                 validators=[DataRequired()])

    username = StringField('Username', validators=[Optional(), Length(max=100)])

    # Chúng ta sẽ không validate password ở đây nếu nó là optional 
    # hoặc nếu việc edit không yêu cầu nhập lại password.
    # Nếu password là bắt buộc khi user_auth_type='Username', cần thêm logic kiểm tra.
    password = PasswordField('Password', validators=[Optional(), Length(max=255)])

    # --- Cấu hình Chứng chỉ Client ---
    client_cert_path = StringField('Đường dẫn Client Certificate', 
                                   validators=[Optional(), Length(max=255)])

    client_key_path = StringField('Đường dẫn Client Private Key', 
                                  validators=[Optional(), Length(max=255)])

    submit = SubmitField('Lưu thông tin')