# app/routes.py
from flask import render_template, redirect, url_for, flash, request, jsonify # Thêm jsonify

from app import db
from app.models import OpcServer, OpcNode
from app.forms import OpcServerForm
from app.opcua_client import (
    connect_server as async_connect_server,
    disconnect_server as async_disconnect_server,
    is_server_connected,
    start_server_browse,
    browse_stop_flags
)
from async_worker import get_async_worker # Import hàm get_async_worker
from asyncua import ua # Import ua để lấy ObjectIds nếu cần
import json # Thêm import json
from app.opcua_client import get_opcua_node_all_attributes # Import hàm mới
from app.opcua_client import async_get_node_data_value # Import hàm mới





# Hàm register_routes sẽ được gọi từ app/__init__.py
def register_routes(app_instance):
    logger = app_instance.logger

    @app_instance.route('/')
    @app_instance.route('/index')
    def index():
        logger.info("Truy cập trang chủ (index).")
        return render_template('index.html', title='Trang Chủ')

    # === Route cho Quản lý OPC UA Servers ===
    @app_instance.route('/servers')
    def list_servers():
        logger.info("Yêu cầu danh sách OPC UA servers.")
        try:
            all_servers = OpcServer.query.order_by(OpcServer.name).all()
            
            server_display_states = {} # Sẽ chứa thông tin để hiển thị
            for srv in all_servers:
                is_live = is_server_connected(srv.id) # Trạng thái runtime
                db_state = srv.connection_status # Trạng thái trong DB
                
                status_text = "Chưa xác định"
                status_class = "secondary" # Bootstrap badge class
                show_connect_button = True
                show_disconnect_button = False

                if db_state == "CONNECTED":
                    if is_live:
                        status_text = "Đã kết nối"
                        status_class = "success"
                        show_connect_button = False
                        show_disconnect_button = True
                    else:
                        # DB nói là CONNECTED, nhưng runtime không thấy -> có thể là lỗi, hoặc đang chờ auto-connect (sẽ làm sau)
                        status_text = "Lỗi/Đang chờ" 
                        status_class = "warning"
                        # Vẫn hiện nút Connect để người dùng có thể thử lại, hoặc nút Disconnect để reset DB state
                        show_connect_button = True 
                        show_disconnect_button = True 
                elif db_state == "DISCONNECTED":
                    status_text = "Chưa kết nối"
                    status_class = "secondary"
                    show_connect_button = True
                    show_disconnect_button = False
                elif db_state == "ERROR":
                    status_text = "Lỗi kết nối"
                    status_class = "danger"
                    show_connect_button = True
                    show_disconnect_button = False # Nếu lỗi, có thể vẫn cho disconnect để reset về DISCONNECTED
                
                server_display_states[srv.id] = {
                    "text": status_text,
                    "class": status_class,
                    "show_connect": show_connect_button,
                    "show_disconnect": show_disconnect_button,
                    "is_live": is_live # Thêm trạng thái runtime thực tế nếu cần
                }
            
            logger.info(f"Tìm thấy {len(all_servers)} server(s). Trạng thái hiển thị: {server_display_states}")
            
            return render_template('servers/list.html',
                                   servers=all_servers,
                                   server_display_states=server_display_states, # Thay thế server_connection_states
                                   title='Danh sách OPC UA Servers')
        except Exception as e:
            logger.error(f"Lỗi khi truy vấn danh sách server: {str(e)}", exc_info=True)
            flash('Không thể tải danh sách server. Vui lòng thử lại sau.', 'danger')
            return render_template('servers/list.html', servers=[], server_display_states={}, title='Danh sách OPC UA Servers')

    @app_instance.route('/servers/add', methods=['GET', 'POST'])
    def add_server():
        form = OpcServerForm()
        if form.validate_on_submit():
            logger.info(f"Form thêm server hợp lệ. Đang xử lý thêm server: {form.name.data}")
            new_server = OpcServer(
                name=form.name.data, endpoint_url=form.endpoint_url.data,
                description=form.description.data,
                security_mode=form.security_mode.data if form.security_mode.data else None,
                security_policy_uri=form.security_policy_uri.data if form.security_policy_uri.data else None,
                user_auth_type=form.user_auth_type.data,
                username=form.username.data if form.username.data else None,
                password=form.password.data if form.password.data else None, # Lưu clear text
                client_cert_path=form.client_cert_path.data if form.client_cert_path.data else None,
                client_key_path=form.client_key_path.data if form.client_key_path.data else None
            )
            try:
                db.session.add(new_server)
                db.session.commit()
                logger.info(f'Đã thêm server "{new_server.name}" (ID: {new_server.id}) thành công!')
                flash(f'Đã thêm server "{new_server.name}" thành công!', 'success')
                return redirect(url_for('list_servers'))
            except Exception as e:
                db.session.rollback()
                logger.error(f"Lỗi khi thêm server '{form.name.data}' vào DB: {str(e)}", exc_info=True)
                flash(f'Lỗi khi thêm server: {str(e)}', 'danger')
        elif request.method == 'POST':
            logger.warning(f"Form thêm server không hợp lệ. Lỗi: {form.errors}")
            flash('Dữ liệu nhập không hợp lệ. Vui lòng kiểm tra lại các trường.', 'warning')
        
        logger.debug("Hiển thị form để thêm server mới (GET request hoặc form không hợp lệ).")
        return render_template('servers/form.html', title='Thêm Server OPC UA Mới', 
                               form_title='Thêm Server OPC UA Mới', form=form,
                               form_action_url=url_for('add_server'),
                               submit_button_text='Thêm Server')

    @app_instance.route('/servers/<int:server_id>/edit', methods=['GET', 'POST'])
    def edit_server(server_id):
        logger.debug(f"Yêu cầu chỉnh sửa server ID: {server_id}")
        server_to_edit = OpcServer.query.get_or_404(server_id)
        form = OpcServerForm(obj=server_to_edit)

        if form.validate_on_submit():
            logger.info(f"Form chỉnh sửa server ID {server_id} ('{server_to_edit.name}') hợp lệ. Đang cập nhật.")
            server_to_edit.name = form.name.data
            server_to_edit.endpoint_url = form.endpoint_url.data
            server_to_edit.description = form.description.data
            server_to_edit.security_mode = form.security_mode.data if form.security_mode.data else None
            server_to_edit.security_policy_uri = form.security_policy_uri.data if form.security_policy_uri.data else None
            server_to_edit.user_auth_type = form.user_auth_type.data
            server_to_edit.username = form.username.data if form.username.data else None
            if form.password.data: # Chỉ cập nhật nếu người dùng nhập mật khẩu mới
                server_to_edit.password = form.password.data
                logger.info(f"Mật khẩu cho server ID {server_id} sẽ được cập nhật.")
            server_to_edit.client_cert_path = form.client_cert_path.data if form.client_cert_path.data else None
            server_to_edit.client_key_path = form.client_key_path.data if form.client_key_path.data else None
            try:
                db.session.commit()
                logger.info(f'Đã cập nhật server "{server_to_edit.name}" (ID: {server_id}) thành công!')
                flash(f'Đã cập nhật server "{server_to_edit.name}" thành công!', 'success')
                return redirect(url_for('list_servers'))
            except Exception as e:
                db.session.rollback()
                logger.error(f"Lỗi khi cập nhật server ID {server_id} ('{server_to_edit.name}'): {str(e)}", exc_info=True)
                flash(f'Lỗi khi cập nhật server: {str(e)}', 'danger')
        elif request.method == 'POST':
            logger.warning(f"Form chỉnh sửa server ID {server_id} không hợp lệ. Lỗi: {form.errors}")
            flash('Dữ liệu cập nhật không hợp lệ. Vui lòng kiểm tra lại các trường.', 'warning')

        logger.debug(f"Hiển thị form chỉnh sửa cho server ID: {server_id} (GET request hoặc form không hợp lệ).")
        return render_template('servers/form.html', title=f'Chỉnh sửa Server: {server_to_edit.name}',
                               form_title=f'Chỉnh sửa Server: {server_to_edit.name}', form=form,
                               server=server_to_edit,
                               form_action_url=url_for('edit_server', server_id=server_id),
                               submit_button_text='Cập nhật Server')

    @app_instance.route('/servers/<int:server_id>/delete', methods=['POST'])
    def delete_server(server_id):
        logger.info(f"Yêu cầu xóa server ID: {server_id}")
        server_to_delete = OpcServer.query.get_or_404(server_id)
        
        if is_server_connected(server_id):
            logger.info(f"Server ID {server_id} đang có kết nối, thử ngắt kết nối trước khi xóa.")
            try:
                worker = get_async_worker()
                worker.run_coroutine(async_disconnect_server(server_id))
            except TimeoutError as te:
                 logger.error(f"Timeout khi ngắt kết nối server ID {server_id} trước khi xóa: {te}", exc_info=True)
            except RuntimeError as re:
                 logger.error(f"Lỗi RuntimeError khi ngắt kết nối server ID {server_id} trước khi xóa: {re}. AsyncWorker có vấn đề?", exc_info=True)
            except Exception as e:
                logger.error(f"Lỗi khác khi ngắt kết nối server ID {server_id} trước khi xóa: {e}", exc_info=True)

        server_name = server_to_delete.name
        try:
            OpcNode.query.filter_by(server_id=server_id).delete() # Xóa các node con trước
            db.session.delete(server_to_delete)
            db.session.commit()
            logger.info(f'Đã xóa server "{server_name}" (ID: {server_id}) và các node liên quan khỏi DB thành công!')
            flash(f'Đã xóa server "{server_name}" và các node liên quan thành công!', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Lỗi khi xóa server ID {server_id} ('{server_name}') khỏi DB: {str(e)}", exc_info=True)
            flash(f'Lỗi khi xóa server: {str(e)}. Có thể server đang được tham chiếu bởi các dữ liệu khác.', 'danger')
        return redirect(url_for('list_servers'))

    # === CÁC ROUTE CHO KẾT NỐI/NGẮT KẾT NỐI OPC UA ===
    @app_instance.route('/servers/<int:server_id>/connect', methods=['POST'])
    def connect_opcua_server(server_id):
        logger.info(f"Yêu cầu kết nối đến server ID: {server_id}")
        server_config = OpcServer.query.get_or_404(server_id)
        
        if is_server_connected(server_id): # Kiểm tra trạng thái runtime trong active_clients
            flash(f'Server "{server_config.name}" đã được kết nối (runtime).', 'info')
            # Vẫn cập nhật DB nếu trạng thái DB là DISCONNECTED
            if server_config.connection_status != "CONNECTED":
                server_config.connection_status = "CONNECTED"
                try:
                    db.session.commit()
                    logger.info(f"DB status cho server ID {server_id} cập nhật thành CONNECTED.")
                except Exception as e_db_update:
                    db.session.rollback()
                    logger.error(f"Lỗi cập nhật DB status cho server ID {server_id} khi đã runtime connected: {e_db_update}")
            return redirect(url_for('list_servers'))
            
        success = False
        try:
            worker = get_async_worker()
            success = worker.run_coroutine(async_connect_server(server_config)) # async_connect_server từ opcua_client.py
            
            if success:
                server_config.connection_status = "CONNECTED"
                flash(f'Kết nối thành công đến server "{server_config.name}".', 'success')
                logger.info(f"Kết nối OPC UA thành công cho server ID {server_id}.")
            else:
                server_config.connection_status = "ERROR" # Hoặc "DISCONNECTED" tùy bạn muốn
                flash(f'Không thể kết nối đến server "{server_config.name}". Xem log để biết chi tiết.', 'danger')
                logger.warning(f"Kết nối OPC UA thất bại cho server ID {server_id}.")
        except TimeoutError as te:
             server_config.connection_status = "ERROR"
             logger.error(f"Timeout khi kết nối server ID {server_id}: {te}", exc_info=True)
             flash(f'Timeout khi cố gắng kết nối đến server "{server_config.name}".', 'danger')
        except RuntimeError as re: # Ví dụ AsyncWorker không chạy
             server_config.connection_status = "ERROR"
             logger.error(f"Lỗi RuntimeError khi kết nối server ID {server_id}: {re}. AsyncWorker có vấn đề?", exc_info=True)
             flash(f'Lỗi hệ thống (AsyncWorker): {str(re)}', 'danger')
        except Exception as e:
            server_config.connection_status = "ERROR"
            logger.error(f"Lỗi không xác định khi kết nối server ID {server_id}: {e}", exc_info=True)
            flash(f'Lỗi không xác định khi cố gắng kết nối đến server "{server_config.name}".', 'danger')
        finally:
            try:
                db.session.commit() # Lưu trạng thái kết nối vào DB
            except Exception as e_commit:
                db.session.rollback()
                logger.error(f"Lỗi khi commit trạng thái kết nối cho server ID {server_id}: {e_commit}", exc_info=True)
                flash('Lỗi khi lưu trạng thái kết nối vào database.', 'danger')
            
        return redirect(url_for('list_servers'))

    @app_instance.route('/servers/<int:server_id>/disconnect', methods=['POST'])
    def disconnect_opcua_server(server_id):
        logger.info(f"Yêu cầu ngắt kết nối khỏi server ID: {server_id}")
        server_config = OpcServer.query.get_or_404(server_id)
        
        # if not is_server_connected(server_id) and server_config.connection_status == "DISCONNECTED":
        #     flash(f'Server "{server_config.name}" chưa được kết nối hoặc đã ngắt kết nối (DB).', 'info')
        #     return redirect(url_for('list_servers'))

        success = False
        try:
            # Vẫn thực hiện ngắt kết nối runtime ngay cả khi DB nói là DISCONNECTED, để đảm bảo
            if is_server_connected(server_id):
                worker = get_async_worker()
                success = worker.run_coroutine(async_disconnect_server(server_id)) # async_disconnect_server từ opcua_client.py
            else:
                success = True # Coi như thành công nếu không có kết nối runtime để ngắt

            if success:
                server_config.connection_status = "DISCONNECTED"
                flash(f'Đã ngắt kết nối khỏi server "{server_config.name}" thành công.', 'success')
                logger.info(f"Ngắt kết nối OPC UA thành công/hoàn tất cho server ID {server_id}.")
            else:
                # Nếu ngắt kết nối runtime thất bại, trạng thái DB vẫn nên là DISCONNECTED (vì ý định là ngắt)
                # hoặc có thể là một trạng thái ERROR_DISCONNECT mới nếu muốn
                server_config.connection_status = "DISCONNECTED" 
                flash(f'Yêu cầu ngắt kết nối cho "{server_config.name}" đã được xử lý (runtime disconnect có thể lỗi).', 'warning')
                logger.warning(f"Ngắt kết nối OPC UA runtime có thể thất bại cho server ID {server_id}, nhưng DB được cập nhật là DISCONNECTED.")
        except TimeoutError as te:
             server_config.connection_status = "DISCONNECTED" # Vẫn coi là disconnected về ý định
             logger.error(f"Timeout khi ngắt kết nối server ID {server_id}: {te}", exc_info=True)
             flash(f'Timeout khi ngắt kết nối server "{server_config.name}".', 'danger')
        except RuntimeError as re:
             server_config.connection_status = "DISCONNECTED"
             logger.error(f"Lỗi RuntimeError khi ngắt kết nối server ID {server_id}: {re}. AsyncWorker có vấn đề?", exc_info=True)
             flash(f'Lỗi hệ thống (AsyncWorker): {str(re)}', 'danger')
        except Exception as e:
            server_config.connection_status = "DISCONNECTED"
            logger.error(f"Lỗi không xác định khi ngắt kết nối server ID {server_id}: {e}", exc_info=True)
            flash(f'Lỗi không xác định khi cố gắng ngắt kết nối khỏi server "{server_config.name}".', 'danger')
        finally:
            try:
                db.session.commit() # Lưu trạng thái kết nối vào DB
            except Exception as e_commit:
                db.session.rollback()
                logger.error(f"Lỗi khi commit trạng thái ngắt kết nối cho server ID {server_id}: {e_commit}", exc_info=True)
                flash('Lỗi khi lưu trạng thái ngắt kết nối vào database.', 'danger')

        return redirect(url_for('list_servers'))

    # === CÁC ROUTE MỚI CHO DUYỆT NODE ===
    @app_instance.route('/servers/<int:server_id>/browse_nodes', methods=['POST'])
    def trigger_browse_and_save_nodes(server_id):
        logger.info(f"Yêu cầu duyệt và lưu node cho server ID: {server_id}")
        opc_server = OpcServer.query.get_or_404(server_id)

        if not is_server_connected(server_id):
            flash(f"Server '{opc_server.name}' chưa được kết nối. Vui lòng kết nối trước khi duyệt node.", "warning")
            return redirect(url_for('list_servers'))

        try:
            max_depth_str = request.form.get('max_depth', '5') 
            max_depth = int(max_depth_str)
            if not (0 <= max_depth <= 10):
                logger.warning(f"Giá trị max_depth không hợp lệ ('{max_depth_str}'), sử dụng mặc định là 5.")
                max_depth = 3
        except ValueError:
            logger.warning(f"Giá trị max_depth không phải số ('{request.form.get('max_depth')}'), sử dụng mặc định là 5.")
            max_depth = 3
        
        logger.info(f"Chuẩn bị duyệt node cho server '{opc_server.name}' (ID: {server_id}) với max_depth={max_depth}.")

        try:
            logger.info(f"Đang xóa các node cũ của server ID {server_id}...")
            num_deleted = OpcNode.query.filter_by(server_id=server_id).delete()
            db.session.commit()
            logger.info(f"Đã xóa {num_deleted} node cũ của server ID {server_id} khỏi DB.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Lỗi khi xóa node cũ của server ID {server_id}: {e}", exc_info=True)
            flash(f"Lỗi khi dọn dẹp node cũ: {str(e)}", "danger")
            return redirect(url_for('list_servers'))

        all_node_data = []
        browse_process_error = None
        
        async def collect_nodes_async_wrapper():
            nonlocal browse_process_error 
            try:
                async for node_info_dict in start_server_browse(server_db_id=server_id, max_depth=max_depth):
                    all_node_data.append(node_info_dict)
            except Exception as e_async_browse:
                browse_process_error = e_async_browse
                logger.error(f"Lỗi bên trong coroutine collect_nodes_async_wrapper cho server ID {server_id}: {e_async_browse}", exc_info=True)
        
        worker = get_async_worker()
        try:
            logger.info(f"Bắt đầu quá trình duyệt bất đồng bộ cho server ID {server_id}...")
            browse_stop_flags.pop(server_id, None) 
            
            worker.run_coroutine(collect_nodes_async_wrapper())
            
            if browse_process_error:
                raise browse_process_error

            logger.info(f"Đã thu thập được {len(all_node_data)} node từ server ID {server_id}.")

        except TimeoutError as te:
             logger.error(f"Timeout khi duyệt node cho server ID {server_id}: {te}", exc_info=True)
             flash(f'Timeout khi duyệt node cho server "{opc_server.name}".', 'danger')
        except RuntimeError as re:
             logger.error(f"Lỗi RuntimeError khi duyệt node cho server ID {server_id}: {re}. AsyncWorker có vấn đề?", exc_info=True)
             flash(f'Lỗi hệ thống (AsyncWorker) khi duyệt node: {str(re)}', 'danger')
        except Exception as e_run:
            logger.error(f"Lỗi trong quá trình duyệt hoặc thu thập node cho server ID {server_id}: {e_run}", exc_info=True)
            flash(f"Có lỗi xảy ra khi duyệt node: {str(e_run)}", "danger")
        
        was_stopped_by_user = browse_stop_flags.pop(server_id, False)
        if was_stopped_by_user:
            logger.info(f"Quá trình duyệt cho server ID {server_id} đã được người dùng yêu cầu dừng.")
            flash("Quá trình duyệt đã được dừng. Đang lưu các node đã tìm thấy (nếu có).", "info")

        if all_node_data:
            nodes_to_add_instances = []
            for node_data_dict in all_node_data:
                try:
                    node_obj = OpcNode(**node_data_dict)
                    nodes_to_add_instances.append(node_obj)
                except TypeError as te:
                    logger.error(f"Lỗi TypeError khi tạo OpcNode từ dict: {node_data_dict}. Lỗi: {te}", exc_info=True)
                except Exception as e_model:
                    logger.error(f"Lỗi khi tạo đối tượng OpcNode từ dict: {node_data_dict}. Lỗi: {e_model}", exc_info=True)
            
            if nodes_to_add_instances:
                try:
                    logger.info(f"Chuẩn bị lưu {len(nodes_to_add_instances)} node vào DB cho server ID {server_id}.")
                    db.session.add_all(nodes_to_add_instances)
                    db.session.commit()
                    logger.info(f"Đã lưu {len(nodes_to_add_instances)} node vào DB cho server ID {server_id}.")
                    flash(f"Đã duyệt và lưu {len(nodes_to_add_instances)} node cho server '{opc_server.name}'.", "success")
                except Exception as e_commit:
                    db.session.rollback()
                    logger.error(f"Lỗi khi commit {len(nodes_to_add_instances)} node cho server ID {server_id}: {e_commit}", exc_info=True)
                    flash(f"Lỗi khi lưu các node đã duyệt: {str(e_commit)}", "danger")
            elif not was_stopped_by_user and not browse_process_error :
                 flash(f"Không tìm thấy node nào để lưu cho server '{opc_server.name}' (dữ liệu duyệt rỗng).", "info")
        
        elif not was_stopped_by_user and not browse_process_error:
            flash(f"Không tìm thấy node nào trên server '{opc_server.name}' với độ sâu đã chọn, hoặc server không có node, hoặc quá trình duyệt gặp lỗi sớm.", "info")
            logger.info(f"Không có node nào được tìm thấy/thu thập cho server ID {server_id} sau khi duyệt.")
        
        return redirect(url_for('list_servers'))


    @app_instance.route('/servers/<int:server_id>/stop_browse', methods=['POST'])
    def stop_browse_for_server(server_id):
        opc_server = OpcServer.query.get_or_404(server_id)
        logger.info(f"Nhận yêu cầu dừng duyệt node cho server ID: {server_id} ({opc_server.name})")
        browse_stop_flags[server_id] = True
        flash(f"Đã gửi yêu cầu dừng duyệt node cho server '{opc_server.name}'. Quá trình sẽ dừng ở điểm kiểm tra tiếp theo.", "info")
        return redirect(request.referrer or url_for('list_servers'))

    @app_instance.route('/servers/<int:server_id>/nodes', methods=['GET'])
    def view_server_nodes(server_id):
        logger.info(f"Yêu cầu xem danh sách node cho server ID: {server_id}")
        opc_server = OpcServer.query.get_or_404(server_id)
        
        try:
            nodes_from_db = OpcNode.query.filter_by(server_id=server_id).all()
            count = len(nodes_from_db)
            logger.info(f"Tìm thấy {count} node trong DB cho server '{opc_server.name}' (ID: {server_id}).")

            if not nodes_from_db:
                flash(f"Không có node nào được lưu trong database cho server '{opc_server.name}'. Bạn có thể cần phải duyệt node trước.", "info")

            # Chuẩn bị dữ liệu cho jsTree
            jstree_data = []
            # NodeId của RootFolder thường là 'i=84', được coi là cha của các node cấp cao nhất như ObjectsFolder
            root_folder_node_id_obj = ua.NodeId(ua.ObjectIds.RootFolder)
            root_folder_node_id_str = root_folder_node_id_obj.to_string()

            for node_db in nodes_from_db:
                parent_id_for_jstree = node_db.parent_node_id_string
                # Nếu parent là RootFolder hoặc không có parent, coi nó là node gốc cho jsTree
                if parent_id_for_jstree is None or parent_id_for_jstree == root_folder_node_id_str:
                    parent_id_for_jstree = "#" # Ký hiệu node gốc cho jsTree

                # Chọn icon dựa trên NodeClass
                icon_class = "fas fa-file-alt text-secondary" # Mặc định
                if node_db.node_class_str == 'Variable':
                    icon_class = "fas fa-tag text-primary"
                elif node_db.node_class_str == 'Object':
                    icon_class = "fas fa-folder text-warning"
                elif node_db.node_class_str == 'Method':
                    icon_class = "fas fa-cog text-info"
                
                jstree_data.append({
                    "id": node_db.node_id_string, # ID của node (NodeId string)
                    "parent": parent_id_for_jstree, # ID của node cha
                    "text": f"{node_db.display_name if node_db.display_name else node_db.browse_name} <small class='text-muted'>({node_db.node_class_str})</small>",
                    "icon": icon_class, # Sử dụng class của Font Awesome
                    "li_attr": {"title": f"NodeId: {node_db.node_id_string}\nBrowseName: {node_db.browse_name}\nClass: {node_db.node_class_str}\nDataType: {node_db.data_type if node_db.data_type else 'N/A'}"},
                    "data": { # Dữ liệu tùy chỉnh bạn muốn gắn với node
                        "db_id": node_db.id,
                        "node_class": node_db.node_class_str,
                        "data_type": node_db.data_type
                    }
                })
            
            # Chuyển đổi sang chuỗi JSON để nhúng vào template
            # Sử dụng 'unsafe_json' filter nếu truyền trực tiếp vào script tag, hoặc truyền như biến context bình thường
            # và JavaScript sẽ lấy từ đó.
            # Để an toàn, chúng ta sẽ truyền như biến context và JavaScript sẽ parse nó.
            # Hoặc, sử dụng json.dumps và |safe filter trong template.
            
            return render_template('nodes/tree.html', 
                                   server=opc_server, 
                                   jstree_data_json=json.dumps(jstree_data), # Truyền dữ liệu JSON đã được dump
                                   nodes_count=count,
                                   title=f"Các Node của Server {opc_server.name}")

        except Exception as e:
            logger.error(f"Lỗi khi truy vấn hoặc xử lý node cho server ID {server_id}: {str(e)}", exc_info=True)
            flash('Không thể tải hoặc xử lý danh sách node từ database.', 'danger')
            return redirect(url_for('list_servers'))
        
    # === ROUTE MỚI CHO AJAX ĐỂ LẤY CHI TIẾT NODE ===
    @app_instance.route('/internal/node_details_ajax/<int:node_db_id>', methods=['GET'])
    def get_node_details_ajax(node_db_id):
        logger.info(f"AJAX request: Lấy chi tiết cho OpcNode DB ID: {node_db_id}")
        
        opc_node_from_db = OpcNode.query.get(node_db_id)
        if not opc_node_from_db:
            logger.warning(f"AJAX request: Không tìm thấy OpcNode với DB ID: {node_db_id}")
            return jsonify({"error": "Node không tìm thấy trong database"}), 404

        opc_server = OpcServer.query.get(opc_node_from_db.server_id)
        if not opc_server:
            logger.error(f"AJAX request: Không tìm thấy OpcServer (ID: {opc_node_from_db.server_id}) cho OpcNode DB ID: {node_db_id}")
            return jsonify({"error": "Server chứa node này không tìm thấy"}), 500

        if not is_server_connected(opc_server.id):
            logger.warning(f"AJAX request: Server '{opc_server.name}' (ID: {opc_server.id}) chưa kết nối. Không thể lấy chi tiết node trực tiếp.")
            # Trả về thông tin từ DB và thông báo server chưa kết nối
            details_from_db = {
                "NodeId": opc_node_from_db.node_id_string,
                "BrowseName": opc_node_from_db.browse_name,
                "DisplayName": opc_node_from_db.display_name,
                "NodeClass": opc_node_from_db.node_class_str,
                "Description": opc_node_from_db.description,
                "DataTypeName": opc_node_from_db.data_type,
                "Value": "(Server chưa kết nối, không thể đọc giá trị)",
                "status_message": f"Server '{opc_server.name}' chưa kết nối. Thông tin hiển thị từ lần duyệt cuối."
            }
            return jsonify(details_from_db)

        # Nếu server đã kết nối, lấy chi tiết từ OPC UA server
        node_details_live = None
        try:
            worker = get_async_worker()
            node_details_live = worker.run_coroutine(
                get_opcua_node_all_attributes(opc_server.id, opc_node_from_db.node_id_string)
            )
        except TimeoutError as te:
            logger.error(f"AJAX request: Timeout khi lấy chi tiết node '{opc_node_from_db.node_id_string}' từ server ID {opc_server.id}: {te}", exc_info=True)
            return jsonify({"error": f"Timeout khi lấy chi tiết node từ server: {str(te)}"}), 504 # Gateway Timeout
        except RuntimeError as re: # Ví dụ AsyncWorker không chạy
            logger.error(f"AJAX request: Lỗi RuntimeError khi lấy chi tiết node '{opc_node_from_db.node_id_string}': {re}", exc_info=True)
            return jsonify({"error": f"Lỗi hệ thống (AsyncWorker): {str(re)}"}), 500
        except Exception as e:
            logger.error(f"AJAX request: Lỗi không xác định khi lấy chi tiết node '{opc_node_from_db.node_id_string}': {e}", exc_info=True)
            return jsonify({"error": f"Lỗi không xác định khi lấy chi tiết node: {str(e)}"}), 500

        if node_details_live:
            return jsonify(node_details_live)
        else:
            # Nếu get_opcua_node_all_attributes trả về None (do lỗi bên trong nó)
            logger.warning(f"AJAX request: Hàm get_opcua_node_all_attributes trả về None cho node '{opc_node_from_db.node_id_string}'.")
            # Có thể trả về thông tin từ DB như một fallback
            details_from_db_fallback = {
                "NodeId": opc_node_from_db.node_id_string,
                "DisplayName": opc_node_from_db.display_name,
                "NodeClass": opc_node_from_db.node_class_str,
                "status_message": "Không thể lấy thông tin trực tiếp từ server OPC UA. Hiển thị thông tin cơ bản từ DB.",
                "Value": "(Không thể đọc giá trị từ server)"
            }
            return jsonify(details_from_db_fallback)
        
    @app_instance.route('/internal/node_value_ajax/<int:node_db_id>', methods=['GET'])
    def get_node_value_ajax(node_db_id):
        logger.info(f"AJAX Refresh Value: Yêu cầu giá trị cho OpcNode DB ID: {node_db_id}")
        
        opc_node_from_db = OpcNode.query.get(node_db_id)
        if not opc_node_from_db:
            logger.warning(f"AJAX Refresh Value: Không tìm thấy OpcNode với DB ID: {node_db_id}")
            return jsonify({"error": "Node không tìm thấy trong database"}), 404

        # Chỉ cho phép làm mới giá trị nếu node là Variable
        if opc_node_from_db.node_class_str != ua.NodeClass.Variable.name: # So sánh với tên của enum
             logger.warning(f"AJAX Refresh Value: Node '{opc_node_from_db.node_id_string}' (Class: {opc_node_from_db.node_class_str}) không phải là Variable.")
             return jsonify({"error": "Chỉ có thể làm mới giá trị cho Node kiểu Variable"}), 400 # Bad Request

        opc_server = OpcServer.query.get(opc_node_from_db.server_id)
        if not opc_server: # Kiểm tra này có thể thừa nếu DB nhất quán
            logger.error(f"AJAX Refresh Value: Không tìm thấy OpcServer cho OpcNode DB ID: {node_db_id}")
            return jsonify({"error": "Server của node không tồn tại"}), 500

        if not is_server_connected(opc_server.id):
            logger.warning(f"AJAX Refresh Value: Server '{opc_server.name}' chưa kết nối.")
            return jsonify({"error": f"Server '{opc_server.name}' chưa kết nối. Không thể làm mới giá trị."}), 503 # Service Unavailable

        value_details = None
        try:
            worker = get_async_worker()
            value_details = worker.run_coroutine(
                async_get_node_data_value(opc_server.id, opc_node_from_db.node_id_string)
            )
        except TimeoutError as te:
            logger.error(f"AJAX Refresh Value: Timeout khi lấy giá trị node '{opc_node_from_db.node_id_string}': {te}", exc_info=True)
            return jsonify({"error": f"Timeout khi lấy giá trị: {str(te)}"}), 504
        except RuntimeError as re:
            logger.error(f"AJAX Refresh Value: Lỗi RuntimeError khi lấy giá trị node '{opc_node_from_db.node_id_string}': {re}", exc_info=True)
            return jsonify({"error": f"Lỗi hệ thống (AsyncWorker): {str(re)}"}), 500
        except Exception as e:
            logger.error(f"AJAX Refresh Value: Lỗi không xác định khi lấy giá trị node '{opc_node_from_db.node_id_string}': {e}", exc_info=True)
            return jsonify({"error": f"Lỗi không xác định: {str(e)}"}), 500

        if value_details:
            if value_details.get("error"): # Nếu hàm async trả về lỗi đã được đóng gói
                # Có thể muốn trả về mã lỗi HTTP khác dựa trên nội dung lỗi
                return jsonify(value_details), 400 # Ví dụ Bad Request nếu node không phải variable từ server
            return jsonify(value_details)
        else:
            logger.warning(f"AJAX Refresh Value: Hàm async_get_node_data_value trả về None cho node '{opc_node_from_db.node_id_string}'.")
            return jsonify({"error": "Không thể lấy giá trị từ server OPC UA."}), 500