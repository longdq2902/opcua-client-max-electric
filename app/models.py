# app/models.py
from app import db # Import đối tượng db từ package app
from datetime import datetime

class OpcServer(db.Model):
    __tablename__ = 'opc_servers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    endpoint_url = db.Column(db.String(255), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)

    # --- Thông tin bảo mật ---
    # SecurityMode: None, Sign, SignAndEncrypt
    security_mode = db.Column(db.String(50), nullable=True)
    
    # SecurityPolicy URI: ví dụ 'http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256'
    security_policy_uri = db.Column(db.String(255), nullable=True)
    
    # --- Thông tin xác thực người dùng (User Identity) ---
    # Loại xác thực: 'Anonymous', 'Username', 'Certificate' (có thể mở rộng)
    user_auth_type = db.Column(db.String(50), default='Anonymous', nullable=False)
    
    username = db.Column(db.String(100), nullable=True)
    
    # Mật khẩu (lưu dưới dạng clear text theo yêu cầu)
    # CẢNH BÁO: Lưu mật khẩu clear text là một rủi ro bảo mật nghiêm trọng trong môi trường production.
    password = db.Column(db.String(255), nullable=True) 

    # --- Thông tin chứng chỉ (Certificate) cho Client Application Authentication và User Authentication (nếu dùng) ---
    # Đường dẫn tới file certificate của client
    client_cert_path = db.Column(db.String(255), nullable=True)
    # Đường dẫn tới file private key của client
    client_key_path = db.Column(db.String(255), nullable=True) 

    connection_status = db.Column(db.String(50), default='DISCONNECTED', nullable=False)

    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<OpcServer {self.name} ({self.endpoint_url})>'
    
    def __repr__(self):
        return f'<OpcServer {self.name} ({self.connection_status})>'

    def get_security_settings(self):
        """
        Trả về một tuple (security_mode, security_policy_uri) 
        hoặc None nếu không được cấu hình.
        """
        if self.security_mode and self.security_policy_uri:
            return (self.security_mode, self.security_policy_uri)
        return None
    
class OpcNode(db.Model):
    __tablename__ = 'opc_nodes'

    id = db.Column(db.Integer, primary_key=True) # Khóa chính của bảng OpcNode
    
    # Khóa ngoại, liên kết đến server mà node này thuộc về
    server_id = db.Column(db.Integer, db.ForeignKey('opc_servers.id'), nullable=False, index=True)
    
    # Thông tin NodeId dưới dạng chuỗi (ví dụ: "ns=2;i=123" hoặc "ns=1;s=MyNode")
    node_id_string = db.Column(db.String(255), nullable=False, index=True)
    
    # BrowseName của node (ví dụ: "TemperatureSensor" hoặc "2:PumpStatus")
    browse_name = db.Column(db.String(255), nullable=True)
    
    # DisplayName của node (tên hiển thị, có thể được bản địa hóa)
    display_name = db.Column(db.String(255), nullable=True)
    
    # NodeClass (ví dụ: "Variable", "Object", "Method", "View", "DataType", "ReferenceType")
    node_class_str = db.Column(db.String(50), nullable=True) # Đổi tên từ node_class để tránh trùng từ khóa Python
    
    # NodeId (dạng chuỗi) của node cha, dùng để tái tạo cấu trúc cây
    # Có thể NULL cho các node gốc hoặc nếu không duyệt từ cha cụ thể
    parent_node_id_string = db.Column(db.String(255), nullable=True, index=True)
    
    # Kiểu dữ liệu của node nếu là Variable (ví dụ: "Int32", "Float", "String")
    data_type = db.Column(db.String(100), nullable=True)
    
    # Mô tả của node (từ thuộc tính Description của node)
    description = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # updated_at có thể không cần thiết cho node nếu thông tin của nó ít thay đổi sau khi duyệt
    # nhưng có thể hữu ích nếu bạn cập nhật thông tin node sau này
    # updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Mối quan hệ với OpcServer (tùy chọn, để dễ truy cập server từ node)
    # server = db.relationship('OpcServer', backref=db.backref('nodes', lazy='dynamic'))
    # Nếu dùng backref, bạn có thể truy cập server.nodes để lấy tất cả node của server đó.
    # 'lazy=dynamic' nghĩa là server.nodes sẽ trả về một query object, không phải list ngay.

    # Ràng buộc duy nhất: một node_id_string phải là duy nhất cho một server_id cụ thể
    __table_args__ = (db.UniqueConstraint('server_id', 'node_id_string', name='uq_server_node_id'),)

    def __repr__(self):
        return f'<OpcNode Srv:{self.server_id} ID:{self.node_id_string} Name:{self.browse_name}>'
    

class SubscriptionMapping(db.Model):
    __tablename__ = 'subscription_mappings'

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=True)

    # Thêm server_id trực tiếp để tạo UNIQUE constraints dễ dàng hơn
    # Giá trị này PHẢI được gán dựa trên server_id của opc_node liên quan
    server_id = db.Column(db.Integer, db.ForeignKey('opc_servers.id'), nullable=False, index=True)
    
    opc_node_db_id = db.Column(db.Integer, db.ForeignKey('opc_nodes.id'), nullable=False, index=True)
    
    ioa_mapping = db.Column(db.Integer, nullable=False, index=True) # Giả sử là số nguyên

    sampling_interval_ms = db.Column(db.Integer, default=1000, nullable=False)
    publishing_interval_ms = db.Column(db.Integer, default=1000, nullable=False)
    
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Mối quan hệ với OpcNode
    opc_node = db.relationship('OpcNode', 
                               foreign_keys=[opc_node_db_id], 
                               backref=db.backref('subscription_mapping_info', lazy='select', uselist=False)) 
                               # uselist=False nếu một OpcNode chỉ có một mapping (do ràng buộc dưới)

    # Mối quan hệ với OpcServer (tùy chọn, nhưng hữu ích cho việc truy vấn)
    opc_server = db.relationship('OpcServer', 
                                 foreign_keys=[server_id],
                                 backref=db.backref('all_subscription_mappings', lazy='dynamic'))

    __table_args__ = (
        # Trong một server, một opc_node_db_id chỉ được map một lần (với một ioa cụ thể).
        db.UniqueConstraint('server_id', 'opc_node_db_id', name='uq_server_node_once_mapping'),
        # Trong một server, một ioa_mapping chỉ được map với một node một lần.
        db.UniqueConstraint('server_id', 'ioa_mapping', name='uq_server_ioa_once_mapping'),
    )

    def __repr__(self):
        return f'<SubscriptionMapping ID:{self.id} SrvID:{self.server_id} NodeDBID:{self.opc_node_db_id} IOA:{self.ioa_mapping} Active:{self.is_active}>'