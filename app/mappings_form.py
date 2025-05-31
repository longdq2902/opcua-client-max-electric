# app/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, PasswordField, SubmitField, IntegerField, BooleanField
from wtforms.validators import DataRequired, Length, URL, Optional, NumberRange
from wtforms_sqlalchemy.fields import QuerySelectField # Import từ thư viện mới
from app.models import OpcServer, OpcNode # Import model

# Hàm factory để tạo query cho OpcServer (dùng cho QuerySelectField)
def opc_server_query():
    return OpcServer.query.order_by(OpcServer.name).all()

# Hàm factory để tạo query cho OpcNode (sẽ được lọc theo server_id ở route)
# Hiện tại để trống, sẽ được cập nhật động trong route
def opc_node_query():
    return OpcNode.query.filter_by(id=-1).all() # Trả về rỗng ban đầu


class SubscriptionMappingForm(FlaskForm):
    description = StringField('Mô tả Mapping', validators=[Optional(), Length(max=255)])
    
    # Trường chọn OpcServer
    opc_server = QuerySelectField('OPC UA Server',
                                  query_factory=opc_server_query,
                                  get_label='name', # Hiển thị trường 'name' của OpcServer
                                  allow_blank=False, # Bắt buộc chọn
                                  validators=[DataRequired(message="Vui lòng chọn một OPC UA Server.")])
    
    # Trường chọn OpcNode (Variable) - sẽ được cập nhật động bằng JavaScript dựa trên server đã chọn
    # Hoặc bạn có thể để người dùng nhập NodeID string trực tiếp nếu không muốn duyệt
    opc_node_db_id = QuerySelectField('OPC UA Node (Variable)',
                                    query_factory=opc_node_query, # Query rỗng ban đầu
                                    get_label='display_name', # Hoặc một trường khác dễ nhận biết
                                    allow_blank=False,
                                    validators=[DataRequired(message="Vui lòng chọn một OPC UA Node.")],
                                    description="Chỉ các node kiểu Variable sẽ hợp lệ.")
                                    # Cần JavaScript để lọc và tải danh sách node này theo server đã chọn.

    ioa_mapping = IntegerField('Giá trị IOA Mapping',
                               validators=[DataRequired(message="Giá trị IOA không được để trống.")])
    
    sampling_interval_ms = IntegerField('Sampling Interval (ms)',
                                        default=1000,
                                        validators=[DataRequired(), NumberRange(min=100, message="Sampling Interval phải ít nhất 100ms.")])
    
    publishing_interval_ms = IntegerField('Publishing Interval (ms)',
                                          default=1000,
                                          validators=[DataRequired(), NumberRange(min=100, message="Publishing Interval phải ít nhất 100ms.")])
    
    is_active = BooleanField('Kích hoạt Subscription này?', default=True)
    
    submit = SubmitField('Lưu Mapping')