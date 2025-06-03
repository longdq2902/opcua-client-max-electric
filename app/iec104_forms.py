from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, SubmitField, BooleanField, SelectField
from wtforms.validators import DataRequired, NumberRange, Optional, IPAddress
from .models import IEC104PointType # Import Enum đã tạo

class IEC104StationForm(FlaskForm):
    name = StringField('Tên Trạm', validators=[DataRequired()])
    ip_address = StringField('Địa chỉ IP Server', validators=[DataRequired(), IPAddress(message="Địa chỉ IP không hợp lệ.")], default='0.0.0.0')
    port = IntegerField('Cổng Server', validators=[DataRequired(), NumberRange(min=1, max=65535)], default=2404)
    common_address = IntegerField('Common Address (CA)', validators=[DataRequired(), NumberRange(min=1)], default=1)

    t0_timeout = IntegerField('T0 Timeout (s)', validators=[DataRequired(), NumberRange(min=1)], default=30)
    t1_timeout = IntegerField('T1 Timeout (s)', validators=[DataRequired(), NumberRange(min=1)], default=15)
    t2_timeout = IntegerField('T2 Timeout (s)', validators=[DataRequired(), NumberRange(min=1)], default=10)
    t3_timeout = IntegerField('T3 Timeout (s)', validators=[DataRequired(), NumberRange(min=1)], default=20)
    k_value = IntegerField('K Value', validators=[DataRequired(), NumberRange(min=1)], default=12)
    w_value = IntegerField('W Value', validators=[DataRequired(), NumberRange(min=1)], default=8)

    submit = SubmitField('Lưu Cấu hình Trạm')

class IEC104PointForm(FlaskForm):
    io_address = IntegerField('Địa chỉ IOA', validators=[DataRequired(), NumberRange(min=0)])
    description = StringField('Mô tả')
    # Lấy các lựa chọn từ Enum IEC104PointType
    point_type_str = SelectField('Loại Điểm Dữ liệu (TypeID)',
                                 choices=[(pt.name, pt.value) for pt in IEC104PointType],
                                 validators=[DataRequired()])
    report_ms = IntegerField('Chu kỳ báo cáo (ms, 0 nếu không dùng)', validators=[Optional(), NumberRange(min=0)], default=0)
    submit = SubmitField('Lưu Điểm Dữ liệu')