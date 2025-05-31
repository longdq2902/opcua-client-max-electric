# app/opcua_client.py
import asyncio
from asyncua import Client as AsyncuaClient # Đổi tên để tránh nhầm lẫn nếu có Client khác
from asyncua import ua
from app.models import OpcServer, SubscriptionMapping, OpcNode
import logging
from asyncua.common.node import Node as AsyncuaNode # Import lớp Node đúng
from typing import Optional # Thêm ở đầu file
from async_worker import get_async_worker
from app import db 



# # Sử dụng logger riêng cho module này hoặc logger của app
# # Trong trường hợp này, chúng ta có thể lấy logger từ app instance khi hàm được gọi từ route,
# # hoặc định nghĩa một logger riêng cho module này.
# # Tạm thời dùng logging module cơ bản.
logger = logging.getLogger(__name__)
# if not logger.handlers: # Tránh thêm handler nhiều lần nếu module được load lại
#     handler = logging.StreamHandler()
#     formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     handler.setFormatter(formatter)
#     logger.addHandler(handler)
#     logger.setLevel(logging.INFO) # Đặt mức log INFO hoặc DEBUG


# Dictionary để lưu trữ các client object đang hoạt động
# Key: server_id (từ database), Value: đối tượng asyncua.Client
active_clients = {}
browse_stop_flags = {}


async def get_server_security_params(server_config: OpcServer):
    """
    Chuẩn bị các tham số bảo mật từ đối tượng OpcServer.
    Đây là một hàm helper, có thể cần điều chỉnh dựa trên cách asyncua yêu cầu.
    """
    params = {}
    if server_config.security_mode and server_config.security_policy_uri:
        # asyncua thường dùng SecurityString dạng: policy_uri#mode
        # Ví dụ: "opc.tcp://server:port/UA/Sample#Basic256Sha256_SignAndEncrypt"
        # Hoặc set_security_string và set_user_identity riêng
        # Hàm set_security của asyncua Client nhận các tham số:
        # policy_uri, certificate_path, private_key_path, server_certificate_path, mode (ua.MessageSecurityMode)

        # Chuyển đổi security_mode từ string sang ua.MessageSecurityMode
        mode_map = {
            "None": ua.MessageSecurityMode.None_,
            "Sign": ua.MessageSecurityMode.Sign,
            "SignAndEncrypt": ua.MessageSecurityMode.SignAndEncrypt,
        }
        params['mode'] = mode_map.get(server_config.security_mode, ua.MessageSecurityMode.None_)
        params['policy_uri'] = server_config.security_policy_uri
        
        if server_config.client_cert_path:
            params['certificate_path'] = server_config.client_cert_path
        if server_config.client_key_path:
            params['private_key_path'] = server_config.client_key_path
        # if server_config.server_cert_path: # Nếu có trường này trong model
        #     params['server_certificate_path'] = server_config.server_cert_path
    
    user_identity = None
    if server_config.user_auth_type == 'Username':
        if server_config.username and server_config.password:
            user_identity = ua.UserIdentityToken(UserName=server_config.username, Password=server_config.password)
        else:
            logger.warning(f"Server {server_config.name} cấu hình Username auth nhưng thiếu username/password.")
    # Thêm xử lý cho 'Certificate' user auth type nếu cần

    return params, user_identity


async def connect_server(server_config: OpcServer):
    """
    Thiết lập kết nối đến một OPC UA server dựa trên cấu hình.
    server_config: Đối tượng OpcServer từ database.
    Trả về True nếu kết nối thành công, False nếu thất bại.
    """
    global active_clients
    server_id = server_config.id

    if server_id in active_clients:
        logger.info(f"Server ID {server_id} ({server_config.name}) đã có kết nối hoạt động.")
        # Kiểm tra lại trạng thái kết nối thực sự của client (nếu cần)
        try:
            # Một cách đơn giản để kiểm tra là đọc một node cơ bản như ServerStatus
            await active_clients[server_id].get_node(ua. γνωστό_NodeId.Server_ServerStatus).read_data_value()
            logger.info(f"Kết nối hiện tại tới server ID {server_id} vẫn còn hiệu lực.")
            return True # Giả sử kết nối vẫn tốt
        except Exception as e:
            logger.warning(f"Kết nối hiện tại tới server ID {server_id} có vấn đề: {e}. Thử ngắt kết nối cũ.")
            await disconnect_server(server_id) # Cố gắng dọn dẹp client cũ

    logger.info(f"Đang thử kết nối đến server: {server_config.name} ({server_config.endpoint_url})")
    # worker = get_async_worker()

    client = AsyncuaClient(url=server_config.endpoint_url, timeout=15)

    # Thiết lập bảo mật (nếu có)
    security_params, user_identity_token = await get_server_security_params(server_config)
    
    if security_params.get('policy_uri') and security_params.get('mode'):
        try:
            logger.info(f"Áp dụng cài đặt bảo mật cho server {server_config.name}: Mode={security_params['mode']}, Policy={security_params['policy_uri']}")
            await client.set_security_string(
                f"{security_params['policy_uri']}#{security_params['mode'].name.replace('_','')}" # Tạo security string
            ) 
            # Hoặc dùng client.set_security() nếu bạn muốn truyền các tham số riêng lẻ
            # await client.set_security(
            #     policy_uri=security_params['policy_uri'],
            #     certificate=security_params.get('certificate_path'),
            #     private_key=security_params.get('private_key_path'),
            #     server_certificate=security_params.get('server_certificate_path'),
            #     mode=security_params['mode']
            # )

            if server_config.client_cert_path and server_config.client_key_path:
                 # asyncua tự động load cert và key nếu security string yêu cầu
                 # Hoặc bạn có thể load thủ công nếu cần thiết:
                 # await client.load_client_certificate(server_config.client_cert_path)
                 # await client.load_private_key(server_config.client_key_path)
                logger.info(f"Sử dụng client certificate: {server_config.client_cert_path}")


        except Exception as e:
            logger.error(f"Lỗi khi thiết lập bảo mật cho server {server_config.name}: {str(e)}", exc_info=True)
            # Không return False ngay, vẫn thử kết nối nếu server cho phép kết nối không bảo mật
            # hoặc nếu security string không bắt buộc.


    # Thiết lập User Identity Token (nếu có)
    if user_identity_token:
        client.user_token = user_identity_token
        logger.info(f"Sử dụng user identity: {server_config.user_auth_type} - Username: {server_config.username}")
    elif server_config.user_auth_type != 'Anonymous':
        logger.warning(f"Server {server_config.name} yêu cầu {server_config.user_auth_type} nhưng thông tin không đủ.")


    try:
        await client.connect()
        logger.info(f"Kết nối thành công đến server: {server_config.name}")
        active_clients[server_id] = client
        return True
    except ConnectionRefusedError:
        logger.error(f"Kết nối bị từ chối từ server: {server_config.name} ({server_config.endpoint_url})")
    except asyncio.TimeoutError:
        logger.error(f"Hết thời gian chờ khi kết nối đến server: {server_config.name} ({server_config.endpoint_url})")
    except Exception as e:
        logger.error(f"Lỗi không xác định khi kết nối đến server {server_config.name}: {str(e)}", exc_info=True)
    
    # Nếu kết nối thất bại, đảm bảo client được đóng (dù có thể chưa connect)
    try:
        await client.disconnect()
    except Exception as e :
        pass # Bỏ qua lỗi khi disconnect client chưa thực sự connect
    return False


async def disconnect_server(server_id: int):
    """
    Ngắt kết nối khỏi một OPC UA server.
    server_id: ID của server trong database.
    Trả về True nếu ngắt kết nối thành công hoặc không có kết nối nào, False nếu lỗi.
    """
    global active_clients
    if server_id in active_clients:
        client = active_clients.pop(server_id) # Lấy và xóa client khỏi danh sách active
        try:
            logger.info(f"Đang ngắt kết nối khỏi server ID: {server_id}")
            await client.disconnect()
            logger.info(f"Đã ngắt kết nối thành công khỏi server ID: {server_id}")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi ngắt kết nối khỏi server ID {server_id}: {str(e)}", exc_info=True)
            # Dù lỗi, client đã được xóa khỏi active_clients
            return False
    else:
        logger.info(f"Không có kết nối hoạt động nào cho server ID: {server_id} để ngắt.")
        return True # Coi như thành công vì không có gì để làm

def get_client_by_server_id(server_id: int) -> Optional[AsyncuaClient]:
    """
    Lấy client object đang hoạt động cho một server_id.
    Trả về client object nếu có, None nếu không.
    """
    return active_clients.get(server_id)

def is_server_connected(server_id: int) -> bool:
   client = active_clients.get(server_id)
   if client and client.uaclient: # Kiểm tra xem client và uaclient (internal client) có tồn tại không
        # Không có cách đơn giản, đồng bộ, và đáng tin cậy 100% để kiểm tra trạng thái "is_connected"
        # của asyncua client mà không thực hiện một lời gọi OPC UA bất đồng bộ.
        # Vì vậy, ở đây chúng ta chỉ kiểm tra sự tồn tại của client object.
        return True
   return False


# mới thêm cho phần duyệt node
async def _browse_recursive(client: AsyncuaClient,
                            ua_node: AsyncuaNode,
                            server_db_id: int,
                            processed_node_ids: set, # Set để theo dõi các node đã xử lý trong phiên này
                            parent_node_db_id_str: str = None,
                            depth: int = 0,
                            max_depth: int = 5):
    """
    Hàm đệ quy để duyệt các node.
    Yield một dictionary chứa thông tin của từng node tìm thấy.
    """
    if browse_stop_flags.get(server_db_id, False):
        logger.info(f"Dừng duyệt (cờ): ServerID={server_db_id}, Node='{ua_node.nodeid.to_string() if ua_node and ua_node.nodeid else 'N/A'}'")
        return

    if not ua_node or not ua_node.nodeid:
        logger.warning(f"Node không hợp lệ hoặc không có NodeId. ServerID={server_db_id}, Parent='{parent_node_db_id_str}', Depth={depth}")
        return

    node_id_str = ua_node.nodeid.to_string()

    if depth > max_depth:
        logger.warning(f"Đạt độ sâu tối đa ({max_depth}). ServerID={server_db_id}, Node='{node_id_str}', Dừng nhánh này.")
        return

    if node_id_str in processed_node_ids:
        logger.debug(f"Node đã xử lý: ServerID={server_db_id}, Node='{node_id_str}'. Bỏ qua.")
        return
    processed_node_ids.add(node_id_str)

    try:
        # Đọc các thuộc tính cơ bản
        try:
            browse_name_obj = await ua_node.read_browse_name()
            browse_name_str = f"{browse_name_obj.NamespaceIndex}:{browse_name_obj.Name}" if browse_name_obj.NamespaceIndex != 0 else browse_name_obj.Name
        except ua.UaStatusCodeError as e_bn:
            logger.warning(f"Lỗi đọc BrowseName: ServerID={server_db_id}, Node='{node_id_str}', Code={e_bn.code}, Msg='{str(e_bn)}'. Bỏ qua node.")
            return

        display_name_str = browse_name_str # Fallback
        try:
            display_name_obj = await ua_node.read_display_name()
            if display_name_obj and display_name_obj.Text:
                display_name_str = display_name_obj.Text
        except ua.UaStatusCodeError as e_dn:
            logger.debug(f"Lỗi đọc DisplayName: ServerID={server_db_id}, Node='{node_id_str}', Code={e_dn.code}, Msg='{str(e_dn)}'. Dùng BrowseName.")

        try:
            node_class_obj = await ua_node.read_node_class()
            node_class_str = node_class_obj.name if node_class_obj else "UnknownNodeClass"
        except ua.UaStatusCodeError as e_nc:
            logger.warning(f"Lỗi đọc NodeClass: ServerID={server_db_id}, Node='{node_id_str}', Code={e_nc.code}, Msg='{str(e_nc)}'. Bỏ qua node.")
            return

        description_str = None
        try:
            description_obj = await ua_node.read_description()
            description_str = description_obj.Text if description_obj and description_obj.Text else None
        except ua.UaStatusCodeError as e_desc:
            logger.debug(f"Lỗi đọc Description: ServerID={server_db_id}, Node='{node_id_str}', Code={e_desc.code}, Msg='{str(e_desc)}'. Gán là None.")

        data_type_str = None
        if node_class_obj == ua.NodeClass.Variable:
            try:
                datatype_nodeid = await ua_node.read_data_type()
                if datatype_nodeid:
                    # Cố gắng lấy tên dễ đọc của DataType, nếu không thì dùng NodeId của nó
                    try:
                        datatype_node = client.get_node(datatype_nodeid)
                        datatype_display_name_obj = await datatype_node.read_display_name()
                        data_type_str = datatype_display_name_obj.Text if datatype_display_name_obj and datatype_display_name_obj.Text else datatype_nodeid.to_string()
                    except Exception as e_dt_resolve:
                        logger.debug(f"Không thể phân giải tên DataType cho {datatype_nodeid} của Node {node_id_str}: {e_dt_resolve}. Dùng NodeId.")
                        data_type_str = datatype_nodeid.to_string()
                else:
                    data_type_str = "DataTypeNotSet"
            except ua.UaStatusCodeError as e_dt_status:
                 logger.debug(f"Lỗi Status Code khi đọc DataType: ServerID={server_db_id}, VariableNode='{node_id_str}', Code={e_dt_status.code}, Msg='{str(e_dt_status)}'. Gán là 'UnknownDataType'.")
                 data_type_str = "UnknownDataType"
            except Exception as e_dt_general:
                logger.debug(f"Lỗi chung khi đọc DataType: ServerID={server_db_id}, VariableNode='{node_id_str}', Lỗi='{str(e_dt_general)}'. Gán là 'UnknownDataType'.")
                data_type_str = "UnknownDataType"

        node_info = {
            'server_id': server_db_id,
            'node_id_string': node_id_str,
            'browse_name': browse_name_str,
            'display_name': display_name_str,
            'node_class_str': node_class_str,
            'parent_node_id_string': parent_node_db_id_str,
            'data_type': data_type_str,
            'description': description_str,
        }
        # logger.info(f"Đã xử lý Node: ServerID={server_db_id}, ID='{node_id_str}', Name='{display_name_str}', Class='{node_class_str}', Parent='{parent_node_db_id_str}', Depth={depth}")
        yield node_info

        # Tiếp tục duyệt các node con
        if node_class_obj in [ua.NodeClass.Object, ua.NodeClass.View] and depth < max_depth:
            if browse_stop_flags.get(server_db_id, False):
                logger.info(f"Dừng duyệt con của node '{node_id_str}' do tín hiệu dừng. ServerID={server_db_id}.")
                return

            children = []
            try:
                children = await ua_node.get_children(refs=ua.ObjectIds.HierarchicalReferences)
            except ua.UaStatusCodeError as e_child:
                 logger.warning(f"Không thể lấy con của node '{node_id_str}': Code={e_child.code}, Msg='{str(e_child)}'. ServerID={server_db_id}.")

            if children:
                logger.debug(f"Node '{node_id_str}' ({display_name_str}) có {len(children)} con. Duyệt con ở độ sâu {depth + 1}. ServerID={server_db_id}.")
                for child_ua_node in children:
                    async for sub_node_info in _browse_recursive(client, child_ua_node, server_db_id,
                                                                 processed_node_ids, # Truyền set
                                                                 node_id_str, depth + 1, max_depth):
                        yield sub_node_info
                    if browse_stop_flags.get(server_db_id, False):
                        logger.info(f"Dừng duyệt các con tiếp theo của node '{node_id_str}' do tín hiệu dừng. ServerID={server_db_id}.")
                        break
    except Exception as e_outer:
        current_node_id_for_log = node_id_str if node_id_str else (ua_node.nodeid.to_string() if ua_node and ua_node.nodeid else 'UnknownNode (outer exception)')
        logger.error(f"Lỗi nghiêm trọng khi xử lý node '{current_node_id_for_log}': {str(e_outer)}. ServerID={server_db_id}.", exc_info=True)


async def start_server_browse(server_db_id: int, start_node_id_str: str = None, max_depth: int = 5):
    """
    Bắt đầu quá trình duyệt Address Space của server.
    Yields node_info dictionaries.
    """
    client = get_client_by_server_id(server_db_id) # Hàm này đã được định nghĩa
    if not client:
        logger.error(f"Không tìm thấy client kết nối cho server ID: {server_db_id}. Không thể duyệt node.")
        return # Hoặc raise Exception

    logger.info(f"Bắt đầu duyệt server ID: {server_db_id}. Độ sâu tối đa: {max_depth}. Đặt cờ dừng về False.")
    browse_stop_flags[server_db_id] = False # Reset/Khởi tạo cờ dừng cho phiên duyệt này
    
    processed_node_ids_this_session = set() # Set để theo dõi node đã xử lý trong phiên này

    try:
        start_ua_node = None
        if start_node_id_str:
            try:
                start_ua_node = client.get_node(start_node_id_str)
                logger.info(f"Bắt đầu duyệt từ node được chỉ định: '{start_node_id_str}'. ServerID={server_db_id}.")
            except Exception as e_get_start_node:
                logger.error(f"Không thể lấy node bắt đầu '{start_node_id_str}': {e_get_start_node}. ServerID={server_db_id}.", exc_info=True)
                return
        else:
            try:
                start_ua_node = client.get_node(ua.ObjectIds.ObjectsFolder)
                logger.info(f"Bắt đầu duyệt từ node gốc ObjectsFolder (NodeId: '{start_ua_node.nodeid.to_string()}'). ServerID={server_db_id}.")
            except Exception as e_get_objects:
                logger.error(f"Không thể lấy node ObjectsFolder: {e_get_objects}. ServerID={server_db_id}.", exc_info=True)
                return
        
        if not start_ua_node: # Nếu vẫn không lấy được start_ua_node
            logger.error(f"Không thể xác định node bắt đầu duyệt. ServerID={server_db_id}.")
            return

        parent_for_start_node = None
        if start_ua_node.nodeid == ua.ObjectIds.ObjectsFolder:
            parent_for_start_node = ua.ObjectIds.RootFolder.to_string() # Cha của ObjectsFolder là RootFolder

        # Bắt đầu duyệt đệ quy
        async for node_data in _browse_recursive(client, start_ua_node, server_db_id, 
                                                 processed_node_ids_this_session, # Truyền set vào
                                                 parent_for_start_node, 0, max_depth):
            yield node_data
        
        # Kiểm tra cờ dừng sau khi generator hoàn tất (hoặc bị dừng sớm)
        if browse_stop_flags.get(server_db_id, False):
            logger.info(f"Quá trình duyệt cho server ID {server_db_id} đã bị dừng bởi cờ.")
        else:
            logger.info(f"Hoàn tất quá trình duyệt (không bị dừng bởi cờ) cho server ID: {server_db_id}")

    except ua.UaError as e_ua:
        logger.error(f"Lỗi OPC UA trong quá trình duyệt server ID {server_db_id}: {str(e_ua)}", exc_info=True)
    except Exception as e_general:
        logger.error(f"Lỗi không mong muốn trong quá trình duyệt server ID {server_db_id}: {str(e_general)}", exc_info=True)
    # Việc dọn dẹp browse_stop_flags[server_db_id] sẽ do route handler thực hiện sau khi asyncio.run() kết thúc
    # để đảm bảo cờ được xóa ngay cả khi generator này có lỗi và không chạy hết.

# kết thúc phần duyệt node

UA_ATTRIBUTES_MAP = {
    ua.AttributeIds.NodeId: "NodeId",
    ua.AttributeIds.NodeClass: "NodeClass",
    ua.AttributeIds.BrowseName: "BrowseName",
    ua.AttributeIds.DisplayName: "DisplayName",
    ua.AttributeIds.Description: "Description",
    ua.AttributeIds.WriteMask: "WriteMask",
    ua.AttributeIds.UserWriteMask: "UserWriteMask",
    ua.AttributeIds.IsAbstract: "IsAbstract",         # Cho DataType, ObjectType, VariableType, ReferenceType
    ua.AttributeIds.Symmetric: "Symmetric",           # Cho ReferenceType
    ua.AttributeIds.InverseName: "InverseName",       # Cho ReferenceType
    ua.AttributeIds.ContainsNoLoops: "ContainsNoLoops", # Cho View
    ua.AttributeIds.EventNotifier: "EventNotifier",     # Cho Object, View
    ua.AttributeIds.Value: "Value",                 # Cho Variable, VariableType
    ua.AttributeIds.DataType: "DataType",             # Cho Variable, VariableType (NodeId của kiểu dữ liệu)
    ua.AttributeIds.ValueRank: "ValueRank",           # Cho Variable, VariableType
    ua.AttributeIds.ArrayDimensions: "ArrayDimensions", # Cho Variable, VariableType
    ua.AttributeIds.AccessLevel: "AccessLevel",         # Cho Variable, VariableType
    ua.AttributeIds.UserAccessLevel: "UserAccessLevel",   # Cho Variable, VariableType
    ua.AttributeIds.MinimumSamplingInterval: "MinimumSamplingInterval", # Cho Variable
    ua.AttributeIds.Historizing: "Historizing",       # Cho Variable
    ua.AttributeIds.Executable: "Executable",         # Cho Method
    ua.AttributeIds.UserExecutable: "UserExecutable",   # Cho Method
    # Bạn có thể thêm các AttributeIds khác nếu cần
    # ví dụ: ua.AttributeIds.DataTypeDefinition (cho DataType)
    # ua.AttributeIds.RolePermissions (nếu dùng Role-based security)
    # ua.AttributeIds.UserRolePermissions (nếu dùng Role-based security)
    # ua.AttributeIds.AccessRestrictions (nếu có)
}

async def get_opcua_node_all_attributes(server_db_id: int, node_id_to_fetch_str: str):
    """
    Lấy tất cả các thuộc tính quan trọng và giá trị (nếu là Variable) của một Node OPC UA cụ thể.
    Trả về một dictionary chứa thông tin chi tiết, hoặc None nếu có lỗi.
    """
    client = get_client_by_server_id(server_db_id)
    if not client:
        logger.error(f"Node Details: Không tìm thấy client kết nối cho server ID: {server_db_id}.")
        return None

    logger.info(f"Node Details: Đang lấy chi tiết cho NodeID '{node_id_to_fetch_str}' trên ServerID {server_db_id}")

    try:
        ua_node = client.get_node(node_id_to_fetch_str)
        if not ua_node:
            logger.warning(f"Node Details: Không thể lấy ua.Node cho NodeID '{node_id_to_fetch_str}'")
            return None

        # Đọc tất cả các thuộc tính được định nghĩa trong UA_ATTRIBUTES_MAP
        # Hoặc bạn có thể chọn một tập con các thuộc tính phổ biến nhất để đọc ban đầu
        attr_ids_to_read = list(UA_ATTRIBUTES_MAP.keys())
        
        # Bỏ ua.AttributeIds.Value ra khỏi danh sách đọc thuộc tính chung,
        # vì nó sẽ được đọc riêng bằng read_data_value() cho Variable node để có thêm thông tin (timestamp, status).
        if ua.AttributeIds.Value in attr_ids_to_read:
            attr_ids_to_read.remove(ua.AttributeIds.Value)
            
        attr_values_data = await ua_node.read_attributes(attr_ids_to_read)
        
        details = {}
        for i, attr_data_value in enumerate(attr_values_data):
            attr_id = attr_ids_to_read[i]
            attr_name = UA_ATTRIBUTES_MAP.get(attr_id) # Lấy tên từ map đã định nghĩa
            
            if attr_data_value.StatusCode.is_good():
                value = attr_data_value.Value.Value
                # Xử lý chuyển đổi giá trị tương tự như trước
                if isinstance(value, ua.NodeId):
                    details[attr_name] = value.to_string()
                elif isinstance(value, ua.QualifiedName):
                    details[attr_name] = f"{value.NamespaceIndex}:{value.Name}" if value.Name else "" # Kiểm tra value.Name
                elif isinstance(value, ua.LocalizedText):
                    details[attr_name] = value.Text if value.Text else "" # Kiểm tra value.Text
                elif attr_id == ua.AttributeIds.NodeClass:
                    if isinstance(value, int): # Nếu giá trị trả về là một số nguyên
                        try:
                            details[attr_name] = ua.NodeClass(value).name # Chuyển số sang tên enum
                        except ValueError:
                            details[attr_name] = f"Unknown NodeClass value ({value})"
                            logger.warning(f"Node Details: Giá trị NodeClass không hợp lệ: {value} cho NodeID '{node_id_to_fetch_str}'")
                    elif isinstance(value, ua.NodeClass): # Nếu đã là đối tượng enum
                        details[attr_name] = value.name
                    else: # Trường hợp khác không mong đợi
                        details[attr_name] = f"Unexpected NodeClass type ({type(value)}): {str(value)}"
                        logger.warning(f"Node Details: Kiểu dữ liệu NodeClass không mong đợi: {type(value)} cho NodeID '{node_id_to_fetch_str}'")
                elif isinstance(value, ua.AccessLevel): # Xử lý cho AccessLevel enum
                     details[attr_name] = value.name
                elif isinstance(value, int) and attr_id in [ua.AttributeIds.AccessLevel, ua.AttributeIds.UserAccessLevel]:
                    try: # Cố gắng chuyển int sang tên enum
                        details[attr_name] = ua.AccessLevel(value).name
                    except ValueError:
                         details[attr_name] = f"Unknown AccessLevel value ({value})"
                elif isinstance(value, int) and attr_id == ua.AttributeIds.ValueRank:
                    if value == -1: details[attr_name] = "Scalar (-1)"
                    elif value == -2: details[attr_name] = "Any (-2)"
                    elif value == -3: details[attr_name] = "ScalarOrOneDimension (-3)"
                    elif value == 0: details[attr_name] = "OneOrMoreDimensions (0)" # Sửa: ValueRank 0 là 1 hoặc nhiều chiều
                    elif value >= 1: details[attr_name] = f"{value}-Dimension Array ({value})"
                    else: details[attr_name] = str(value)
                elif isinstance(value, bool) and attr_id == ua.AttributeIds.Historizing: # Historizing là boolean
                    details[attr_name] = value
                elif isinstance(value, list) and attr_id == ua.AttributeIds.ArrayDimensions: # ArrayDimensions là list các int
                    details[attr_name] = value if value else "Not an Array or Unknown" # Nếu rỗng, có thể là scalar
                else:
                    details[attr_name] = str(value) if value is not None else None
            else:
                details[attr_name] = f"Lỗi đọc: {attr_data_value.StatusCode.name} ({attr_data_value.StatusCode.value})"
        
        # Nếu là Variable, đọc giá trị và kiểu dữ liệu thực tế
        if details.get("NodeClass") == "Variable":
            try:
                data_value_obj = await ua_node.read_data_value() # Đổi tên biến để tránh nhầm lẫn
                if data_value_obj.StatusCode.is_good():
                    details["Value"] = str(data_value_obj.Value.Value)
                    details["ValueSourceTimestamp"] = data_value_obj.SourceTimestamp.isoformat() if data_value_obj.SourceTimestamp else None
                    details["ValueServerTimestamp"] = data_value_obj.ServerTimestamp.isoformat() if data_value_obj.ServerTimestamp else None
                    details["ValueStatusCode"] = data_value_obj.StatusCode.name
                    
                    # Lấy tên kiểu dữ liệu dễ đọc (nếu có thể) từ DataType NodeId đã đọc ở trên
                    datatype_node_id_str = details.get("DataType") # DataType giờ là NodeId string từ map
                    if datatype_node_id_str:
                        try:
                            dt_node = client.get_node(datatype_node_id_str)
                            dt_name_obj = await dt_node.read_display_name() # Đổi tên biến
                            details["DataTypeName"] = dt_name_obj.Text if dt_name_obj and dt_name_obj.Text else datatype_node_id_str
                        except Exception as e_dt_name:
                            logger.debug(f"Node Details: Không thể lấy DisplayName cho DataTypeNodeId {datatype_node_id_str}: {e_dt_name}")
                            details["DataTypeName"] = datatype_node_id_str # Fallback về NodeId
                else:
                    details["Value"] = f"Lỗi đọc giá trị: {data_value_obj.StatusCode.name}"
                    details["ValueStatusCode"] = data_value_obj.StatusCode.name
            except Exception as e_val:
                logger.error(f"Node Details: Lỗi khi đọc giá trị của Variable '{node_id_to_fetch_str}': {e_val}", exc_info=True)
                details["Value"] = "Lỗi đọc giá trị"
        
        logger.info(f"Node Details: Lấy thành công chi tiết cho NodeID '{node_id_to_fetch_str}'.")
        return details

    # ... (phần xử lý exception còn lại của hàm) ...
    except ua.UaError as e_ua:
        logger.error(f"Node Details: Lỗi OPC UA khi lấy chi tiết NodeID '{node_id_to_fetch_str}': {str(e_ua)}", exc_info=True)
    except Exception as e_general:
        logger.error(f"Node Details: Lỗi không mong muốn khi lấy chi tiết NodeID '{node_id_to_fetch_str}': {str(e_general)}", exc_info=True)
    
    return None

async def async_get_node_data_value(server_db_id: int, node_id_to_fetch_str: str):
    """
    Chỉ đọc DataValue (bao gồm Value, StatusCode, Timestamps) của một Variable Node.
    Trả về một dictionary chứa thông tin DataValue, hoặc None nếu có lỗi.
    """
    client = get_client_by_server_id(server_db_id)
    if not client:
        logger.error(f"Refresh Value: Không tìm thấy client kết nối cho server ID: {server_db_id}.")
        return {"error": "Server không kết nối"}

    logger.info(f"Refresh Value: Đang đọc DataValue cho NodeID '{node_id_to_fetch_str}' trên ServerID {server_db_id}")

    try:
        ua_node = client.get_node(node_id_to_fetch_str)
        if not ua_node:
            logger.warning(f"Refresh Value: Không thể lấy ua.Node cho NodeID '{node_id_to_fetch_str}'")
            return {"error": "Node không tồn tại trên server"}

        # Kiểm tra nhanh xem có phải Variable không, mặc dù lý tưởng là client đã biết điều này
        # node_class_obj = await ua_node.read_node_class()
        # if node_class_obj != ua.NodeClass.Variable:
        #     logger.warning(f"Refresh Value: NodeID '{node_id_to_fetch_str}' không phải là Variable.")
        #     return {"error": "Node không phải là Variable"}

        data_value_obj = await ua_node.read_data_value()
        
        value_details = {}
        if data_value_obj.StatusCode.is_good():
            value_details["Value"] = str(data_value_obj.Value.Value)
            value_details["ValueSourceTimestamp"] = data_value_obj.SourceTimestamp.isoformat() if data_value_obj.SourceTimestamp else None
            value_details["ValueServerTimestamp"] = data_value_obj.ServerTimestamp.isoformat() if data_value_obj.ServerTimestamp else None
            value_details["ValueStatusCode"] = data_value_obj.StatusCode.name
        else:
            value_details["Value"] = f"Lỗi đọc: {data_value_obj.StatusCode.name}"
            value_details["ValueStatusCode"] = data_value_obj.StatusCode.name
            value_details["error_message"] = f"Không thể đọc giá trị: {data_value_obj.StatusCode.name}"
        
        logger.info(f"Refresh Value: Đọc DataValue thành công cho NodeID '{node_id_to_fetch_str}'.")
        return value_details

    except ua.UaError as e_ua:
        logger.error(f"Refresh Value: Lỗi OPC UA khi đọc DataValue cho NodeID '{node_id_to_fetch_str}': {str(e_ua)}", exc_info=True)
        return {"error": f"Lỗi OPC UA: {str(e_ua)}"}
    except Exception as e_general:
        logger.error(f"Refresh Value: Lỗi không mong muốn khi đọc DataValue cho NodeID '{node_id_to_fetch_str}': {str(e_general)}", exc_info=True)
        return {"error": f"Lỗi không mong muốn: {str(e_general)}"}
    

    # Hàm tự động kết nối 
def try_auto_reconnect_servers(app_instance): # <-- Thêm app_instance làm tham số
    """
    Cố gắng tự động kết nối lại các server.
    Cần được gọi bên trong một app_context hoặc truyền app_instance.
    """
    logger.info("Bắt đầu quá trình kiểm tra và tự động kết nối lại các server...")
    try:
        # Sử dụng app_context của app_instance được truyền vào
        with app_instance.app_context():
            servers_to_check = OpcServer.query.filter_by(connection_status="CONNECTED").all()
            
            if not servers_to_check:
                logger.info("Không có server nào trong CSDL được đánh dấu là 'CONNECTED' để kiểm tra.")
                return

            worker = get_async_worker()
            if not worker.loop or not worker.loop.is_running():
                logger.error("AsyncWorker không chạy, không thể thực hiện tự động kết nối lại.")
                return

            changes_made_to_db = False
            for server_config in servers_to_check:
                if not is_server_connected(server_config.id):
                    logger.info(f"Server '{server_config.name}' (ID: {server_config.id}) được đánh dấu 'CONNECTED' trong DB "
                                f"nhưng không có kết nối runtime. Đang thử kết nối lại...")
                    try:
                        # async_connect_server được giả định là đã được import hoặc định nghĩa
                        # success = worker.run_coroutine(async_connect_server(server_config))
                        success = worker.run_coroutine(connect_server(server_config)) # <-- SỬA Ở ĐÂY
                        if success:
                            logger.info(f"Tự động kết nối lại thành công cho server '{server_config.name}'.")
                            # Trạng thái DB đã là CONNECTED, không cần thay đổi nếu thành công
                        else:
                            logger.warning(f"Tự động kết nối lại thất bại cho server '{server_config.name}'. "
                                           f"Cập nhật trạng thái DB thành 'ERROR'.")
                            server_config.connection_status = "ERROR"
                            db.session.add(server_config)
                            changes_made_to_db = True
                    except Exception as e_connect:
                        logger.error(f"Lỗi nghiêm trọng khi tự động kết nối lại server '{server_config.name}': {e_connect}", exc_info=True)
                        server_config.connection_status = "ERROR"
                        db.session.add(server_config)
                        changes_made_to_db = True
                else:
                    logger.info(f"Server '{server_config.name}' (ID: {server_config.id}) đã có kết nối runtime.")
            
            if changes_made_to_db:
                try:
                    db.session.commit()
                    logger.info("Đã commit các thay đổi trạng thái server vào DB sau khi auto-reconnect.")
                except Exception as e_commit:
                    db.session.rollback()
                    logger.error(f"Lỗi khi commit thay đổi trạng thái server sau auto-reconnect: {e_commit}", exc_info=True)
            else:
                logger.info("Không có thay đổi trạng thái nào cần commit sau khi auto-reconnect.")

        logger.info("Hoàn tất quá trình kiểm tra và tự động kết nối lại.")

    except Exception as e:
        # Không nên có db.session.rollback() ở đây nếu khối with app_instance.app_context() đã bao trùm
        # vì context có thể đã bị hủy. Rollback nên nằm trong khối with.
        logger.error(f"Lỗi nghiêm trọng trong quá trình try_auto_reconnect_servers: {e}", exc_info=True)

# app/opcua_client.py
import asyncio
import logging
import requests # Cần cho việc gọi API bên thứ 3

from asyncua import Client as AsyncuaClient
from asyncua import ua
from asyncua.common.node import Node as AsyncuaNode

# Các import từ app của bạn (đảm bảo chúng tồn tại và đúng đường dẫn)
# from app import db # Sẽ cần nếu SubHandler hoặc các hàm khác cần truy cập DB trực tiếp
# from app.models import SubscriptionMapping, OpcNode # Tương tự
from async_worker import get_async_worker # Giả sử async_worker.py cùng cấp trong app

# --- Logger Setup (Giữ nguyên hoặc điều chỉnh nếu cần) ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# --- Global Dictionaries (Giữ nguyên các dict đã có) ---
active_clients = {}
browse_stop_flags = {}

# Dictionary mới để lưu trữ các OPC UA subscription đang hoạt động
# Key: mapping_id (ID từ bảng subscription_mappings trong DB)
# Value: tuple (asyncua_subscription_object, monitored_item_handle, handler_instance)
active_opcua_subscriptions = {}

# --- Các hàm đã có: get_server_security_params, connect_server, disconnect_server,
# get_client_by_server_id, is_server_connected, _browse_recursive, start_server_browse,
# get_opcua_node_all_attributes, async_get_node_data_value ---
# (Giữ nguyên các hàm này như phiên bản cuối cùng của chúng)


# --- LỚP VÀ CÁC HÀM MỚI CHO SUBSCRIPTION ---

class SubHandler:
    """
    Xử lý các thông báo thay đổi dữ liệu từ OPC UA Subscription.
    Mỗi monitored item sẽ có một instance của handler này.
    """
    def __init__(self, mapping_id: int, ioa_mapping_value: int, node_id_str: str, server_id: int):
        self.mapping_id = mapping_id
        self.ioa_mapping = ioa_mapping_value
        self.node_id_str = node_id_str # NodeID của OPC UA node đang được theo dõi
        self.server_id = server_id # Server ID mà node này thuộc về
        self.api_url = "http://localhost:5001/api/v1/datapoint-value" # API cố định
        self.worker_loop = get_async_worker().loop # Lấy event loop từ AsyncWorker

    async def datachange_notification(self, node: AsyncuaNode, val, data):
        """
        Được gọi bởi asyncua khi có thay đổi dữ liệu.
        'node': đối tượng asyncua.common.node.Node của item được monitor.
        'val': giá trị mới của node.
        'data': đối tượng DataChangeNotification.
        """
        source_timestamp = data.monitored_item.Value.SourceTimestamp
        server_timestamp = data.monitored_item.Value.ServerTimestamp
        status_code = data.monitored_item.Value.StatusCode

        logger.info(
            f"SubHandler (MappingID: {self.mapping_id}, Node: {self.node_id_str}): "
            f"DataChange! Value={val}, StatusCode={status_code.name if status_code else 'N/A'}, "
            f"SourceTs={source_timestamp}, ServerTs={server_timestamp}"
        )

        if status_code and not status_code.is_good():
            logger.warning(
                f"SubHandler (MappingID: {self.mapping_id}, Node: {self.node_id_str}): "
                f"Nhận được DataChange với StatusCode không tốt: {status_code.name}. Không gọi API."
            )
            return

        payload = {
            "ioa": self.ioa_mapping,
            "value": val
            # Bạn có thể thêm timestamp hoặc các thông tin khác vào payload nếu API thứ 3 hỗ trợ
            # "timestamp": source_timestamp.isoformat() if source_timestamp else None
        }

        try:
            logger.debug(f"SubHandler (MappingID: {self.mapping_id}): Gọi API '{self.api_url}' với payload: {payload}")
            
            # Thực hiện lời gọi API (blocking) trong một executor để không chặn event loop
            response = await self.worker_loop.run_in_executor(
                None,  # Sử dụng ThreadPoolExecutor mặc định
                lambda: requests.post(self.api_url, json=payload, timeout=10) # timeout 10 giây
            )
            
            logger.info(
                f"SubHandler (MappingID: {self.mapping_id}): Gọi API thành công. "
                f"Status: {response.status_code}, Response (100 chars): {response.text[:100]}"
            )
            if response.status_code >= 300: # 3xx, 4xx, 5xx là các mã lỗi hoặc redirect không mong muốn
                 logger.warning(
                     f"SubHandler (MappingID: {self.mapping_id}): API trả về mã lỗi: "
                     f"{response.status_code} - {response.text}"
                 )

        except requests.exceptions.Timeout:
            logger.error(
                f"SubHandler (MappingID: {self.mapping_id}, Node: {self.node_id_str}): "
                f"Timeout khi gọi API: {self.api_url}", exc_info=False # Không cần full traceback cho timeout
            )
        except requests.exceptions.RequestException as req_e:
            logger.error(
                f"SubHandler (MappingID: {self.mapping_id}, Node: {self.node_id_str}): "
                f"Lỗi RequestException khi gọi API: {req_e}", exc_info=True
            )
        except Exception as e:
            logger.error(
                f"SubHandler (MappingID: {self.mapping_id}, Node: {self.node_id_str}): "
                f"Lỗi không xác định khi gọi API: {e}", exc_info=True
            )

    def event_notification(self, event):
        """Được gọi bởi asyncua khi có thông báo event (ít dùng cho data change đơn giản)."""
        logger.info(
            f"SubHandler (MappingID: {self.mapping_id}, Node: {self.node_id_str}): "
            f"Event Notification: {event}"
        )


async def actual_subscribe_opcua_node(server_id: int, node_id_str: str, ioa_value: int,
                                    sampling_ms: int, publishing_ms: int,
                                    mapping_db_id: int): # mapping_db_id để theo dõi và làm key
    """
    Thực hiện việc tạo subscription và monitored item cho một mapping cụ thể.
    """
    global active_opcua_subscriptions
    
    logger.info(
        f"Attempting Subscribe: ServerID={server_id}, NodeID='{node_id_str}', "
        f"IOA={ioa_value}, MappingDBID={mapping_db_id}, Sampling={sampling_ms}ms, Publishing={publishing_ms}ms"
    )

    if mapping_db_id in active_opcua_subscriptions:
        logger.warning(f"Mapping ID {mapping_db_id} (Node: '{node_id_str}') đã được subscribe từ trước. Bỏ qua.")
        return True # Coi như thành công nếu đã có

    client = get_client_by_server_id(server_id)
    if not client:
        logger.error(f"Không thể subscribe node '{node_id_str}': Server ID {server_id} chưa kết nối.")
        return False

    try:
        ua_node_to_subscribe = client.get_node(node_id_str)
        if not ua_node_to_subscribe: # Kiểm tra thêm
            logger.error(f"Không thể lấy đối tượng ua.Node cho '{node_id_str}' trên server ID {server_id}.")
            return False
            
        # Tạo handler, truyền các thông tin cần thiết
        handler_instance = SubHandler(mapping_id=mapping_db_id,
                                      ioa_mapping_value=ioa_value,
                                      node_id_str=node_id_str,
                                      server_id=server_id)
        
        # Tạo subscription object trên server
        # period là publishing interval (tính bằng ms)
        subscription = await client.create_subscription(period=publishing_ms, handler=handler_instance)
        logger.info(
            f"Đã tạo subscription object trên server cho MappingID {mapping_db_id}. "
            f"SubId: {subscription.subscription_id}, Period: {publishing_ms}ms"
        )

        # Thêm Monitored Item vào subscription
        # sampling_interval (tính bằng ms)
        monitored_item_handle = await subscription.subscribe_data_change(
            nodes=ua_node_to_subscribe, # Có thể truyền một node hoặc list các node
            attr=ua.AttributeIds.Value,
            queuesize=1, 
            sampling_interval=sampling_ms # asyncua v0.9.93+ chấp nhận ms trực tiếp
        )
        logger.info(
            f"Đã subscribe DataChange cho node '{node_id_str}' (MappingID: {mapping_db_id}). "
            f"Handle: {monitored_item_handle}. Sampling: {sampling_ms}ms"
        )
        
        active_opcua_subscriptions[mapping_db_id] = (subscription, monitored_item_handle, handler_instance)
        return True
        
    except ua.UaStatusCodeError as e_status:
        logger.error(
            f"Lỗi StatusCode khi subscribe node '{node_id_str}' (MappingID: {mapping_db_id}): "
            f"{e_status.code} - {str(e_status)}", exc_info=True
        )
    except Exception as e:
        logger.error(
            f"Lỗi không xác định khi subscribe node '{node_id_str}' (MappingID: {mapping_db_id}): {e}",
            exc_info=True
        )
    return False


async def unsubscribe_from_mapping(mapping_id: int):
    """
    Hủy một OPC UA subscription đang hoạt động dựa trên mapping_id.
    """
    global active_opcua_subscriptions
    logger.info(f"Attempting to unsubscribe MappingID: {mapping_id}")

    if mapping_id in active_opcua_subscriptions:
        subscription_obj, monitored_item_handle, _ = active_opcua_subscriptions.pop(mapping_id)
        try:
            if monitored_item_handle and subscription_obj:
                logger.info(f"Unsubscribing monitored item (handle: {monitored_item_handle}) for MappingID {mapping_id}.")
                await subscription_obj.unsubscribe(monitored_item_handle)
            if subscription_obj:
                logger.info(f"Deleting subscription object (SubId: {subscription_obj.subscription_id}) for MappingID {mapping_id}.")
                await subscription_obj.delete()
            logger.info(f"Đã hủy subscription thành công cho MappingID {mapping_id}.")
            return True
        except ua.UaError as e_ua:
            logger.error(f"Lỗi OPC UA khi unsubscribe/delete cho MappingID {mapping_id}: {e_ua}", exc_info=True)
        except Exception as e:
            logger.error(f"Lỗi không xác định khi unsubscribe/delete cho MappingID {mapping_id}: {e}", exc_info=True)
        # Dù lỗi, đã xóa khỏi active_opcua_subscriptions
        return False 
    else:
        logger.warning(f"Không tìm thấy active OPC UA subscription cho MappingID {mapping_id} để unsubscribe.")
        return True # Không có gì để làm, coi như thành công
    
def subscribe_all_active_mappings_runtime(app_instance_for_context): # Cần app_context để query DB
    """
    Thử subscribe tất cả các SubscriptionMapping đang có is_active = True trong CSDL
    và server tương ứng đang kết nối, mà chưa có subscription runtime.
    Hàm này là đồng bộ, nó sẽ điều phối các coroutine qua AsyncWorker.
    """
    logger.info("Bắt đầu quá trình 'Subscribe All Active Mappings'.")
    subscribed_count = 0
    failed_count = 0
    already_subscribed_count = 0
    server_not_connected_count = 0

    try:
        with app_instance_for_context.app_context(): # Đảm bảo có app context
            active_mappings_in_db = SubscriptionMapping.query.filter_by(is_active=True).all()

            if not active_mappings_in_db:
                logger.info("Không có mapping nào được đánh dấu 'is_active=True' trong CSDL.")
                return {"total": 0, "success": 0, "failed": 0, "skipped_already_subscribed":0, "skipped_server_disconnected":0}

            worker = get_async_worker()
            if not worker.loop or not worker.loop.is_running():
                logger.error("AsyncWorker không chạy, không thể thực hiện 'Subscribe All'.")
                return {"error": "AsyncWorker not running"}

            for mapping in active_mappings_in_db:
                if mapping.id in active_opcua_subscriptions:
                    logger.info(f"Mapping ID {mapping.id} (IOA: {mapping.ioa_mapping}) đã được subscribe runtime. Bỏ qua.")
                    already_subscribed_count += 1
                    continue

                if not is_server_connected(mapping.server_id):
                    logger.warning(f"Server ID {mapping.server_id} cho Mapping ID {mapping.id} (IOA: {mapping.ioa_mapping}) chưa kết nối. Bỏ qua subscribe.")
                    server_not_connected_count +=1
                    continue
                
                opc_node = mapping.opc_node # Sử dụng relationship đã có
                if not opc_node or opc_node.node_class_str != ua.NodeClass.Variable.name:
                    logger.warning(f"Mapping ID {mapping.id} trỏ đến node không hợp lệ hoặc không phải Variable. Bỏ qua.")
                    failed_count += 1 # Coi như một lỗi cấu hình
                    continue
                
                logger.info(f"Đang thử subscribe cho Mapping ID {mapping.id} (IOA: {mapping.ioa_mapping})...")
                try:
                    success = worker.run_coroutine(
                        actual_subscribe_opcua_node(
                            server_id=mapping.server_id,
                            node_id_str=opc_node.node_id_string,
                            ioa_value=mapping.ioa_mapping,
                            sampling_ms=mapping.sampling_interval_ms,
                            publishing_ms=mapping.publishing_interval_ms,
                            mapping_db_id=mapping.id
                        )
                    )
                    if success:
                        subscribed_count += 1
                    else:
                        failed_count += 1
                except Exception as e_sub:
                    logger.error(f"Lỗi khi subscribe cho Mapping ID {mapping.id}: {e_sub}", exc_info=True)
                    failed_count += 1
            
            logger.info(f"'Subscribe All Active Mappings' hoàn tất. Thành công: {subscribed_count}, Thất bại: {failed_count}, Đã sub từ trước: {already_subscribed_count}, Server chưa kết nối: {server_not_connected_count}")
            return {"total": len(active_mappings_in_db), "success": subscribed_count, "failed": failed_count, "skipped_already_subscribed": already_subscribed_count, "skipped_server_disconnected": server_not_connected_count}

    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng trong quá trình 'Subscribe All Active Mappings': {e}", exc_info=True)
        return {"error": str(e)}


def unsubscribe_all_runtime_subscriptions_opcua(): # Không cần app_context vì chỉ thao tác với active_opcua_subscriptions và worker
    """
    Hủy tất cả các OPC UA subscription đang hoạt động trong active_opcua_subscriptions.
    Hàm này là đồng bộ, nó sẽ điều phối các coroutine qua AsyncWorker.
    """
    logger.info("Bắt đầu quá trình 'Unsubscribe All Runtime Subscriptions'.")
    unsubscribed_count = 0
    failed_count = 0
    
    # Sao chép danh sách keys vì dictionary sẽ thay đổi trong vòng lặp
    mapping_ids_to_unsubscribe = list(active_opcua_subscriptions.keys())

    if not mapping_ids_to_unsubscribe:
        logger.info("Không có subscription runtime nào đang hoạt động để hủy.")
        return {"total_runtime": 0, "success": 0, "failed": 0}

    worker = get_async_worker()
    if not worker.loop or not worker.loop.is_running():
        logger.error("AsyncWorker không chạy, không thể thực hiện 'Unsubscribe All'.")
        return {"error": "AsyncWorker not running"}

    for mapping_id in mapping_ids_to_unsubscribe:
        logger.info(f"Đang thử unsubscribe cho Mapping ID {mapping_id}...")
        try:
            success = worker.run_coroutine(unsubscribe_from_mapping(mapping_id))
            if success:
                unsubscribed_count += 1
            else:
                failed_count += 1
        except Exception as e_unsub:
            logger.error(f"Lỗi khi unsubscribe cho Mapping ID {mapping_id}: {e_unsub}", exc_info=True)
            failed_count += 1
            # active_opcua_subscriptions[mapping_id] có thể vẫn còn nếu run_coroutine lỗi trước khi pop
            # nhưng unsubscribe_from_mapping đã pop nó ra rồi.

    logger.info(f"'Unsubscribe All Runtime Subscriptions' hoàn tất. Thành công: {unsubscribed_count}, Thất bại: {failed_count}, Tổng số đã thử: {len(mapping_ids_to_unsubscribe)}")
    return {"total_runtime_before": len(mapping_ids_to_unsubscribe), "success": unsubscribed_count, "failed": failed_count}