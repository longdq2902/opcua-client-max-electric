# app/mappings_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app 
from app import db
from app.models import OpcServer, OpcNode, SubscriptionMapping
from app.opcua_client import (
    actual_subscribe_opcua_node as async_subscribe_from_mapping, # <-- SỬA Ở ĐÂY
    unsubscribe_from_mapping as async_unsubscribe_from_mapping, # Đảm bảo hàm này cũng có trong opcua_client.py
    is_server_connected,
    active_opcua_subscriptions, # Để kiểm tra trạng thái runtime
    subscribe_all_active_mappings_runtime, # HÀM MỚI
    unsubscribe_all_runtime_subscriptions_opcua # HÀM MỚI
)
from async_worker import get_async_worker
from sqlalchemy.exc import IntegrityError
from asyncua import ua # Để lấy NodeClass.Variable.name
from app.mappings_form import SubscriptionMappingForm, opc_node_query


mappings_bp = Blueprint('mappings', __name__, url_prefix='/mappings')

# Helper function để lấy logger từ app hiện tại
def get_logger():
    from flask import current_app
    return current_app.logger

@mappings_bp.route('/')
def list_mappings():
    logger = get_logger()
    logger.info("Yêu cầu danh sách Subscription Mappings.")
    try:
        all_mappings = SubscriptionMapping.query.order_by(SubscriptionMapping.id.desc()).all()

        mapping_runtime_states = {}
        server_states_for_template = {} # <-- KHỞI TẠO BIẾN NÀY

        for m in all_mappings:
            # Lấy trạng thái runtime của subscription
            mapping_runtime_states[m.id] = m.id in active_opcua_subscriptions
            
            # Lấy và lưu trạng thái kết nối của server cho mapping này (nếu chưa có)
            # Điều này cần thiết để template biết server có đang kết nối hay không
            # khi quyết định có disable nút "Sub" runtime hay không.
            if m.server_id is not None and m.server_id not in server_states_for_template:
                server_states_for_template[m.server_id] = {"is_live": is_server_connected(m.server_id)}

        logger.info(f"Runtime subscription states: {mapping_runtime_states}")
        logger.info(f"Server connection states for template: {server_states_for_template}")

        return render_template('mappings/list_mappings.html', 
                               mappings=all_mappings,
                               mapping_runtime_states=mapping_runtime_states,
                               server_states_for_template=server_states_for_template, # <-- TRUYỀN BIẾN NÀY VÀO TEMPLATE
                               title="Quản lý Subscription Mappings")
    except Exception as e:
        logger.error(f"Lỗi khi tải danh sách mappings: {str(e)}", exc_info=True)
        flash("Không thể tải danh sách mappings.", "danger")
        return redirect(url_for('index'))

@mappings_bp.route('/add', methods=['GET', 'POST'])
def add_mapping():
    logger = get_logger()
    form = SubscriptionMappingForm()

    # Nếu là POST request, server đã được chọn ở lần GET hoặc từ dữ liệu POST.
    # Chúng ta cần populate choices cho opc_node_db_id dựa trên server được submit.
    # form.opc_server.data sẽ là đối tượng OpcServer sau khi process_formdata của nó được gọi.
    # Tuy nhiên, để validation của opc_node_db_id hoạt động, query của nó cần được set
    # dựa trên server_id đã chọn.
    
    # Lấy server_id từ request.form nếu là POST, vì form.opc_server.data có thể chưa được populate đúng ý
    # trước khi toàn bộ form được validate.
    if request.method == 'POST':
        selected_server_id_from_form = request.form.get('opc_server') # Đây là ID string
        if selected_server_id_from_form:
            try:
                server_id_int = int(selected_server_id_from_form)
                # Cập nhật query cho opc_node_db_id TRƯỚC KHI VALIDATE
                form.opc_node_db_id.query = OpcNode.query.filter_by(
                    server_id=server_id_int,
                    node_class_str=ua.NodeClass.Variable.name
                ).order_by(OpcNode.display_name)
            except ValueError:
                 logger.error(f"Giá trị opc_server không hợp lệ từ form POST: {selected_server_id_from_form}")
                 form.opc_node_db_id.query = opc_node_query() # Query rỗng
        else:
            # Nếu không có opc_server nào được gửi, giữ query rỗng
            form.opc_node_db_id.query = opc_node_query()


    if form.validate_on_submit(): # Bây giờ opc_node_db_id đã có query đúng để validate
        logger.info(f"Form thêm mapping hợp lệ. IOA: {form.ioa_mapping.data}")
        
        # form.opc_server.data bây giờ là đối tượng OpcServer
        # form.opc_node_db_id.data bây giờ là đối tượng OpcNode
        selected_opc_node = form.opc_node_db_id.data 
        # (Không cần query lại OpcNode.query.get(...) nữa vì QuerySelectField đã làm điều đó)

        # Server ID cũng có thể lấy từ form.opc_server.data.id
        server_id_for_check = form.opc_server.data.id

        # Không cần kiểm tra selected_opc_node is None nữa vì DataRequired đã làm
        # Không cần kiểm tra node_class_str nữa vì query của opc_node_db_id đã lọc theo Variable

        # Kiểm tra ràng buộc UNIQUE (server_id, opc_node_db_id) và (server_id, ioa_mapping)
        existing_node_mapping = SubscriptionMapping.query.filter_by(server_id=server_id_for_check, opc_node_db_id=selected_opc_node.id).first()
        if existing_node_mapping:
            flash(f"Node '{selected_opc_node.display_name}' đã được map trong server này.", "danger")
            # Cần populate lại query cho node nếu render lại form
            form.opc_node_db_id.query = OpcNode.query.filter_by(server_id=server_id_for_check, node_class_str=ua.NodeClass.Variable.name).order_by(OpcNode.display_name)
            return render_template('mappings/mapping_form.html', title="Thêm Mapping Mới", form=form, form_title="Thêm Mapping Mới", form_action_url=url_for('mappings.add_mapping'))

        existing_ioa_mapping = SubscriptionMapping.query.filter_by(server_id=server_id_for_check, ioa_mapping=form.ioa_mapping.data).first()
        if existing_ioa_mapping:
            flash(f"Giá trị IOA '{form.ioa_mapping.data}' đã được sử dụng cho một node khác trong server này.", "danger")
            form.opc_node_db_id.query = OpcNode.query.filter_by(server_id=server_id_for_check, node_class_str=ua.NodeClass.Variable.name).order_by(OpcNode.display_name)
            return render_template('mappings/mapping_form.html', title="Thêm Mapping Mới", form=form, form_title="Thêm Mapping Mới", form_action_url=url_for('mappings.add_mapping'))

        new_mapping = SubscriptionMapping(
            description=form.description.data,
            server_id=server_id_for_check, # Lấy server_id từ server đã chọn
            opc_node_db_id=selected_opc_node.id,
            ioa_mapping=form.ioa_mapping.data,
            sampling_interval_ms=form.sampling_interval_ms.data,
            publishing_interval_ms=form.publishing_interval_ms.data,
            is_active=form.is_active.data
        )
        try:
            db.session.add(new_mapping)
            db.session.commit()
            logger.info(f"Đã thêm mapping mới ID: {new_mapping.id} cho IOA {new_mapping.ioa_mapping}")
            flash("Mapping mới đã được thêm thành công.", "success")

            if new_mapping.is_active and is_server_connected(new_mapping.server_id):
                logger.info(f"Mapping ID {new_mapping.id} is_active, thử subscribe...")
                worker = get_async_worker()
                try:
                    success_sub = worker.run_coroutine(
                        async_subscribe_from_mapping( # Đây là actual_subscribe_opcua_node
                            server_id=new_mapping.server_id,
                            node_id_str=selected_opc_node.node_id_string,
                            ioa_value=new_mapping.ioa_mapping,
                            sampling_ms=new_mapping.sampling_interval_ms,
                            publishing_ms=new_mapping.publishing_interval_ms,
                            mapping_db_id=new_mapping.id
                        )
                    )
                    if success_sub:
                         flash(f"Đã kích hoạt subscription cho mapping IOA {new_mapping.ioa_mapping}.", "info")
                    else:
                         flash(f"Không thể kích hoạt subscription cho mapping IOA {new_mapping.ioa_mapping}.", "warning")
                except Exception as e_sub:
                    logger.error(f"Lỗi khi tự động subscribe mapping mới ID {new_mapping.id}: {e_sub}", exc_info=True)
                    flash(f"Lỗi khi tự động subscribe mapping mới: {str(e_sub)}", "warning")
            
            return redirect(url_for('mappings.list_mappings'))
        # ... (except IntegrityError, Exception như cũ) ...
        except IntegrityError as e: 
            db.session.rollback()
            logger.error(f"Lỗi IntegrityError khi thêm mapping: {e}", exc_info=True)
            flash(f"Lỗi khi thêm mapping: Dữ liệu có thể bị trùng lặp. {str(e.orig)}", "danger")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Lỗi không xác định khi thêm mapping: {e}", exc_info=True)
            flash(f"Lỗi không xác định khi thêm mapping: {str(e)}", "danger")
    
    # Xử lý cho GET request hoặc khi POST không validate và cần render lại form
    # Đảm bảo opc_node_db_id.query được set nếu opc_server đã có giá trị (ví dụ, khi form load lại sau lỗi validation)
    if form.opc_server.data and isinstance(form.opc_server.data, OpcServer):
        # form.opc_server.data là đối tượng OpcServer nếu GET request với dữ liệu mẫu hoặc POST bị lỗi validation
        form.opc_node_db_id.query = OpcNode.query.filter_by(
            server_id=form.opc_server.data.id,
            node_class_str=ua.NodeClass.Variable.name
        ).order_by(OpcNode.display_name)
    elif not hasattr(form.opc_node_db_id, 'query') or form.opc_node_db_id.query is None : # Nếu query chưa được set
        form.opc_node_db_id.query = opc_node_query() # Query rỗng mặc định

    return render_template('mappings/mapping_form.html', title="Thêm Mapping Mới", form=form, form_title="Thêm Mapping Mới", form_action_url=url_for('mappings.add_mapping'))
# Route để lấy danh sách node (Variable) cho một server (dùng cho AJAX)

@mappings_bp.route('/get_nodes_for_server/<int:server_id>')
def get_nodes_for_server_ajax(server_id):
    logger = get_logger()
    logger.info(f"AJAX: Yêu cầu danh sách Variable nodes cho server ID: {server_id}")
    # Chỉ lấy các node là Variable và đã được duyệt/lưu vào DB
    nodes = OpcNode.query.filter_by(server_id=server_id, node_class_str=ua.NodeClass.Variable.name)\
                         .order_by(OpcNode.display_name).all()
    nodes_data = [{"id": node.id, "text": f"{node.display_name or node.browse_name} ({node.node_id_string})"} for node in nodes]
    return jsonify(nodes_data)


# Thêm route cho edit_mapping(mapping_id)
@mappings_bp.route('/<int:mapping_id>/edit', methods=['GET', 'POST'])
def edit_mapping(mapping_id):
    logger = get_logger()
    logger.info(f"Edit item: {mapping_id}")
    mapping_to_edit = SubscriptionMapping.query.get_or_404(mapping_id)
    
    # Tạo form. Nếu là GET, WTForms sẽ cố gắng dùng obj để điền dữ liệu.
    # Nếu là POST, nó sẽ nạp dữ liệu từ request.form.
    form = SubscriptionMappingForm(obj=mapping_to_edit if request.method == 'GET' else None)

    # --- Xử lý QuerySelectField cho OpcNode ---
    # 1. Cho GET request (khi form được tải lần đầu để chỉnh sửa):
    #    Populate danh sách node dựa trên server_id của mapping đang sửa.
    if request.method == 'GET':
        if mapping_to_edit.opc_server: # opc_server là relationship object
            form.opc_node_db_id.query = OpcNode.query.filter_by(
                server_id=mapping_to_edit.server_id, # Lấy server_id từ mapping
                node_class_str=ua.NodeClass.Variable.name
            ).order_by(OpcNode.display_name)
            # Giá trị hiện tại của node sẽ được WTForms tự chọn nếu obj được truyền
            # và form.opc_node_db_id.data được gán đúng (thường là gán object OpcNode)
            form.opc_node_db_id.data = mapping_to_edit.opc_node # Gán object node hiện tại
        else:
            # Trường hợp hiếm: mapping có server_id nhưng không load được opc_server object
            form.opc_node_db_id.query = opc_node_query() # Query rỗng

    # 2. Cho POST request (khi người dùng submit form):
    #    Populate danh sách node dựa trên server_id người dùng đã chọn trong form (có thể đã thay đổi)
    #    Điều này QUAN TRỌNG để validation của opc_node_db_id hoạt động đúng.
    if request.method == 'POST':
        selected_server_id_from_form_str = request.form.get('opc_server') # ID của OpcServer được chọn
        if selected_server_id_from_form_str:
            try:
                server_id_int = int(selected_server_id_from_form_str)
                form.opc_node_db_id.query = OpcNode.query.filter_by(
                    server_id=server_id_int,
                    node_class_str=ua.NodeClass.Variable.name
                ).order_by(OpcNode.display_name)
            except ValueError:
                logger.error(f"Edit Mapping: Giá trị opc_server không hợp lệ từ form POST: {selected_server_id_from_form_str}")
                form.opc_node_db_id.query = opc_node_query() # Query rỗng
        else:
            # Nếu không có opc_server nào được gửi (lỗi), giữ query rỗng
            form.opc_node_db_id.query = opc_node_query()


    if form.validate_on_submit():
        logger.info(f"Form sửa mapping ID {mapping_id} hợp lệ. IOA mới: {form.ioa_mapping.data}")
        
        selected_opc_node = form.opc_node_db_id.data # Đây là đối tượng OpcNode
        selected_opc_server = form.opc_server.data # Đây là đối tượng OpcServer

        # Kiểm tra NodeClass (mặc dù query đã lọc, kiểm tra lại cho chắc)
        if selected_opc_node.node_class_str != ua.NodeClass.Variable.name:
            flash("Node đã chọn không phải là Variable. Vui lòng chọn một Variable node.", "danger")
            # Cần populate lại query cho node nếu render lại form
            form.opc_node_db_id.query = OpcNode.query.filter_by(server_id=selected_opc_server.id, node_class_str=ua.NodeClass.Variable.name).order_by(OpcNode.display_name)
            return render_template('mappings/mapping_form.html', title="Chỉnh sửa Mapping", form=form, form_title=f"Chỉnh sửa Mapping ID: {mapping_id}", form_action_url=url_for('mappings.edit_mapping', mapping_id=mapping_id))

        server_id_for_check = selected_opc_server.id

        # Kiểm tra ràng buộc UNIQUE khi sửa, ngoại trừ chính mapping đang sửa
        existing_node_mapping = SubscriptionMapping.query.filter(
            SubscriptionMapping.server_id == server_id_for_check,
            SubscriptionMapping.opc_node_db_id == selected_opc_node.id,
            SubscriptionMapping.id != mapping_id
        ).first()
        if existing_node_mapping:
            flash(f"Node '{selected_opc_node.display_name}' đã được map bởi một mapping khác trong server '{selected_opc_server.name}'.", "danger")
            form.opc_node_db_id.query = OpcNode.query.filter_by(server_id=server_id_for_check, node_class_str=ua.NodeClass.Variable.name).order_by(OpcNode.display_name)
            return render_template('mappings/mapping_form.html', title="Chỉnh sửa Mapping", form=form, form_title=f"Chỉnh sửa Mapping ID: {mapping_id}", form_action_url=url_for('mappings.edit_mapping', mapping_id=mapping_id))

        existing_ioa_mapping = SubscriptionMapping.query.filter(
            SubscriptionMapping.server_id == server_id_for_check,
            SubscriptionMapping.ioa_mapping == form.ioa_mapping.data,
            SubscriptionMapping.id != mapping_id
        ).first()
        if existing_ioa_mapping:
            flash(f"Giá trị IOA '{form.ioa_mapping.data}' đã được sử dụng cho một node khác trong server '{selected_opc_server.name}'.", "danger")
            form.opc_node_db_id.query = OpcNode.query.filter_by(server_id=server_id_for_check, node_class_str=ua.NodeClass.Variable.name).order_by(OpcNode.display_name)
            return render_template('mappings/mapping_form.html', title="Chỉnh sửa Mapping", form=form, form_title=f"Chỉnh sửa Mapping ID: {mapping_id}", form_action_url=url_for('mappings.edit_mapping', mapping_id=mapping_id))

        # Lưu lại các giá trị cũ để so sánh và quyết định có re-subscribe không
        old_is_active = mapping_to_edit.is_active
        old_server_id = mapping_to_edit.server_id
        old_opc_node_db_id = mapping_to_edit.opc_node_db_id
        old_sampling_ms = mapping_to_edit.sampling_interval_ms
        old_publishing_ms = mapping_to_edit.publishing_interval_ms
        old_ioa_mapping = mapping_to_edit.ioa_mapping # IOA thay đổi cũng nên re-subscribe

        # Cập nhật thông tin cho mapping_to_edit
        mapping_to_edit.description = form.description.data
        mapping_to_edit.server_id = selected_opc_server.id 
        mapping_to_edit.opc_node_db_id = selected_opc_node.id
        mapping_to_edit.ioa_mapping = form.ioa_mapping.data
        mapping_to_edit.sampling_interval_ms = form.sampling_interval_ms.data
        mapping_to_edit.publishing_interval_ms = form.publishing_interval_ms.data
        mapping_to_edit.is_active = form.is_active.data
        
        try:
            db.session.commit()
            logger.info(f"Đã cập nhật mapping ID: {mapping_id}")
            flash("Mapping đã được cập nhật thành công.", "success")

            worker = get_async_worker()
            
            # Kiểm tra xem có cần re-subscribe không
            params_changed = (
                old_server_id != mapping_to_edit.server_id or
                old_opc_node_db_id != mapping_to_edit.opc_node_db_id or
                old_sampling_ms != mapping_to_edit.sampling_interval_ms or
                old_publishing_ms != mapping_to_edit.publishing_interval_ms or
                old_ioa_mapping != mapping_to_edit.ioa_mapping # Nếu IOA thay đổi, handler cần thông tin mới
            )

            needs_unsubscribe = False
            if mapping_id in active_opcua_subscriptions:
                if not mapping_to_edit.is_active or params_changed:
                    needs_unsubscribe = True
            
            if needs_unsubscribe:
                logger.info(f"Mapping ID {mapping_id}: Cần unsubscribe do is_active=False hoặc tham số thay đổi.")
                worker.run_coroutine(async_unsubscribe_from_mapping(mapping_id))
                flash(f"Subscription cũ cho mapping (IOA: {old_ioa_mapping}) đã được hủy (nếu có).", "info")
            
            if mapping_to_edit.is_active and is_server_connected(mapping_to_edit.server_id):
                if needs_unsubscribe or (mapping_id not in active_opcua_subscriptions): # Re-subscribe nếu vừa unsub hoặc chưa có sub
                    logger.info(f"Mapping ID {mapping_id} is_active=True, thử subscribe/re-subscribe...")
                    current_opc_node_obj = OpcNode.query.get(mapping_to_edit.opc_node_db_id) # Lấy node object mới nhất
                    if current_opc_node_obj:
                        success_sub = worker.run_coroutine(
                            async_subscribe_from_mapping( 
                                server_id=mapping_to_edit.server_id,
                                node_id_str=current_opc_node_obj.node_id_string,
                                ioa_value=mapping_to_edit.ioa_mapping,
                                sampling_ms=mapping_to_edit.sampling_interval_ms,
                                publishing_ms=mapping_to_edit.publishing_interval_ms,
                                mapping_db_id=mapping_to_edit.id
                            )
                        )
                        if success_sub:
                            flash(f"Đã (thử) kích hoạt/cập nhật subscription cho mapping IOA {mapping_to_edit.ioa_mapping}.", "info")
                        else:
                            flash(f"Không thể kích hoạt/cập nhật subscription cho mapping IOA {mapping_to_edit.ioa_mapping}.", "warning")
                    else:
                        flash(f"Không tìm thấy OpcNode DB ID {mapping_to_edit.opc_node_db_id} để re-subscribe.", "danger")
            
            return redirect(url_for('mappings.list_mappings'))
            
        except IntegrityError as e:
            db.session.rollback()
            logger.error(f"Lỗi IntegrityError khi cập nhật mapping ID {mapping_id}: {e}", exc_info=True)
            flash(f"Lỗi khi cập nhật mapping: Dữ liệu có thể bị trùng lặp. {str(e.orig)}", "danger")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Lỗi không xác định khi cập nhật mapping ID {mapping_id}: {e}", exc_info=True)
            flash(f"Lỗi không xác định khi cập nhật mapping: {str(e)}", "danger")

    # Cho GET request, gán lại form.opc_server.data để đảm bảo dropdown server hiển thị đúng
    # và query cho node cũng được set ở đầu hàm rồi.
    # form.opc_node_db_id.data sẽ được tự động chọn bởi WTForms nếu giá trị mapping_to_edit.opc_node
    # nằm trong danh sách các lựa chọn của form.opc_node_db_id.query.
    if request.method == 'GET':
        form.opc_server.data = mapping_to_edit.opc_server # Quan trọng để JS có thể lấy server_id đúng
        # form.opc_node_db_id.data đã được set bởi obj=mapping_to_edit
        # nhưng để chắc chắn nó được chọn đúng trong dropdown đã được populate bởi query,
        # bạn có thể gán lại nếu cần, nhưng thường thì không.
        # form.opc_node_db_id.data = mapping_to_edit.opc_node

    return render_template('mappings/mapping_form.html', 
                           title="Chỉnh sửa Mapping", 
                           form=form, 
                           form_title=f"Chỉnh sửa Mapping ID: {mapping_id}",
                           mapping_id=mapping_id, # Có thể cần cho JavaScript nếu muốn lấy ID server ban đầu
                        #    current_server_id_for_js = mapping_to_edit.server_id, # Truyền server_id ban đầu cho JS
                        #    current_node_id_for_js = mapping_to_edit.opc_node_db_id, # Truyền node_id ban đầu cho JS
                           js_initial_server_id = mapping_to_edit.server_id, # SỬA Ở ĐÂY
                           js_initial_node_id = mapping_to_edit.opc_node_db_id, # SỬA Ở ĐÂY
                           form_action_url=url_for('mappings.edit_mapping', mapping_id=mapping_id))

# TODO: Thêm route cho delete_mapping(mapping_id)

@mappings_bp.route('/<int:mapping_id>/delete', methods=['POST'])
def delete_mapping(mapping_id):
    logger = get_logger()
    mapping_to_delete = SubscriptionMapping.query.get_or_404(mapping_id)
    mapping_desc = f"Mapping ID {mapping_id} (IOA: {mapping_to_delete.ioa_mapping}, Node: {mapping_to_delete.opc_node.node_id_string if mapping_to_delete.opc_node else 'N/A'})"

    logger.info(f"Yêu cầu xóa {mapping_desc}")
    try:
        # 1. Hủy subscription OPC UA đang hoạt động (nếu có)
        if mapping_id in active_opcua_subscriptions:
            logger.info(f"{mapping_desc} đang có active subscription. Thực hiện unsubscribe.")
            worker = get_async_worker()
            try:
                # Giả sử async_unsubscribe_from_mapping là tên hàm đúng
                worker.run_coroutine(async_unsubscribe_from_mapping(mapping_id))
                logger.info(f"Đã unsubscribe thành công cho {mapping_desc} trước khi xóa.")
            except Exception as e_unsub:
                logger.error(f"Lỗi khi unsubscribe {mapping_desc} trước khi xóa: {e_unsub}", exc_info=True)
                flash(f"Lưu ý: Có lỗi khi hủy subscription runtime cho {mapping_desc}, nhưng vẫn sẽ xóa khỏi DB.", "warning")
        
        # 2. Xóa mapping khỏi database
        db.session.delete(mapping_to_delete)
        db.session.commit()
        logger.info(f"Đã xóa {mapping_desc} thành công khỏi DB.")
        flash(f"{mapping_desc} đã được xóa thành công.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Lỗi khi xóa {mapping_desc}: {e}", exc_info=True)
        flash(f"Lỗi khi xóa {mapping_desc}: {str(e)}", "danger")
    
    return redirect(url_for('mappings.list_mappings'))
# Thêm route cho toggle_active_mapping(mapping_id) -> gọi subscribe/unsubscribe
@mappings_bp.route('/<int:mapping_id>/runtime_subscribe', methods=['POST'])
def runtime_subscribe_single_mapping(mapping_id):
    logger = get_logger()
    mapping = SubscriptionMapping.query.get_or_404(mapping_id)
    opc_node_obj = mapping.opc_node

    logger.info(f"Yêu cầu Runtime Subscribe cho Mapping ID: {mapping.id} (IOA: {mapping.ioa_mapping})")

    if not mapping.is_active:
        flash(f"Mapping IOA {mapping.ioa_mapping} không được kích hoạt (is_active=False) trong CSDL. Không thể subscribe.", "warning")
        return redirect(url_for('mappings.list_mappings'))

    if not opc_node_obj:
        flash(f"Không tìm thấy thông tin OPC Node cho mapping ID {mapping.id}.", "danger")
        return redirect(url_for('mappings.list_mappings'))

    if not is_server_connected(mapping.server_id):
        flash(f"Server '{mapping.opc_server.name}' chưa kết nối. Không thể subscribe mapping IOA {mapping.ioa_mapping}.", "warning")
        return redirect(url_for('mappings.list_mappings'))

    if mapping.id in active_opcua_subscriptions:
        flash(f"Mapping IOA {mapping.ioa_mapping} đã được subscribe runtime từ trước.", "info")
        return redirect(url_for('mappings.list_mappings'))

    try:
        worker = get_async_worker()
        success_sub = worker.run_coroutine(
            async_subscribe_from_mapping( # Đây là actual_subscribe_opcua_node
                server_id=mapping.server_id,
                node_id_str=opc_node_obj.node_id_string,
                ioa_value=mapping.ioa_mapping,
                sampling_ms=mapping.sampling_interval_ms,
                publishing_ms=mapping.publishing_interval_ms,
                mapping_db_id=mapping.id
            )
        )
        if success_sub:
            flash(f"Đã thực hiện subscribe runtime thành công cho Mapping IOA {mapping.ioa_mapping}.", "success")
        else:
            flash(f"Không thể thực hiện subscribe runtime cho Mapping IOA {mapping.ioa_mapping}. Kiểm tra log.", "danger")
    except Exception as e:
        logger.error(f"Lỗi khi thực hiện runtime subscribe cho Mapping ID {mapping.id}: {e}", exc_info=True)
        flash(f"Lỗi khi thực hiện runtime subscribe: {str(e)}", "danger")
    
    return redirect(url_for('mappings.list_mappings'))

@mappings_bp.route('/<int:mapping_id>/runtime_unsubscribe', methods=['POST'])
def runtime_unsubscribe_single_mapping(mapping_id):
    logger = get_logger()
    mapping = SubscriptionMapping.query.get_or_404(mapping_id) # Lấy để biết thông tin cho flash message
    
    logger.info(f"Yêu cầu Runtime Unsubscribe cho Mapping ID: {mapping.id} (IOA: {mapping.ioa_mapping})")

    if mapping.id not in active_opcua_subscriptions:
        flash(f"Mapping IOA {mapping.ioa_mapping} không có subscription runtime đang hoạt động để hủy.", "info")
        return redirect(url_for('mappings.list_mappings'))

    try:
        worker = get_async_worker()
        success_unsub = worker.run_coroutine(async_unsubscribe_from_mapping(mapping.id))
        if success_unsub:
            flash(f"Đã thực hiện unsubscribe runtime thành công cho Mapping IOA {mapping.ioa_mapping}.", "success")
        else:
            flash(f"Có lỗi khi thực hiện unsubscribe runtime cho Mapping IOA {mapping.ioa_mapping}.", "warning")
    except Exception as e:
        logger.error(f"Lỗi khi thực hiện runtime unsubscribe cho Mapping ID {mapping.id}: {e}", exc_info=True)
        flash(f"Lỗi khi thực hiện runtime unsubscribe: {str(e)}", "danger")

    return redirect(url_for('mappings.list_mappings'))

# TODO: Thêm route cho subscribe_all / unsubscribe_all
@mappings_bp.route('/subscribe_all', methods=['POST'])
def subscribe_all_action():
    logger = get_logger()
    logger.info("Yêu cầu 'Subscribe All Active Mappings'.")
    # Hàm subscribe_all_active_mappings_runtime cần app_context để query DB
    # current_app là proxy đến app hiện tại, có thể dùng để lấy context
    # hoặc truyền current_app._get_current_object()
    results = subscribe_all_active_mappings_runtime(current_app._get_current_object())
    
    if results.get("error"):
        flash(f"Lỗi khi thực hiện Subscribe All: {results['error']}", "danger")
    else:
        flash(f"Subscribe All: {results.get('success',0)} thành công, {results.get('failed',0)} thất bại, "
              f"{results.get('skipped_already_subscribed',0)} đã sub, {results.get('skipped_server_disconnected',0)} server chưa kết nối.", "info")
    return redirect(url_for('mappings.list_mappings'))

@mappings_bp.route('/unsubscribe_all', methods=['POST'])
def unsubscribe_all_action():
    logger = get_logger()
    logger.info("Yêu cầu 'Unsubscribe All Runtime Subscriptions'.")
    results = unsubscribe_all_runtime_subscriptions_opcua()

    if results.get("error"):
        flash(f"Lỗi khi thực hiện Unsubscribe All: {results['error']}", "danger")
    else:
        flash(f"Unsubscribe All: {results.get('success',0)} thành công, {results.get('failed',0)} thất bại "
              f"(trên tổng số {results.get('total_runtime_before',0)} đang chạy).", "info")
    return redirect(url_for('mappings.list_mappings'))

@mappings_bp.route('/<int:mapping_id>/db_only_toggle_active', methods=['POST'])
def db_only_toggle_active(mapping_id):
    logger = get_logger()
    mapping = SubscriptionMapping.query.get_or_404(mapping_id)
    db_action = request.form.get('db_action') # Mong đợi 'activate_db' hoặc 'deactivate_db'

    logger.info(f"Yêu cầu DB ONLY toggle active cho Mapping ID: {mapping_id}, DB Action: {db_action}")

    if db_action not in ['activate_db', 'deactivate_db']:
        flash("Hành động không hợp lệ cho DB toggle.", "danger")
        return redirect(url_for('mappings.list_mappings'))

    new_active_state_db = (db_action == 'activate_db')
    action_text = "kích hoạt (DB)" if new_active_state_db else "vô hiệu hóa (DB)"

    if mapping.is_active == new_active_state_db:
        flash(f"Mapping ID {mapping.id} đã ở trạng thái {action_text} trong CSDL.", "info")
    else:
        mapping.is_active = new_active_state_db
        db.session.add(mapping)
        try:
            db.session.commit()
            flash(f"Đã {action_text} cho Mapping ID {mapping.id} trong CSDL.", "success")
            logger.info(f"Đã cập nhật is_active = {new_active_state_db} (DB only) cho Mapping ID {mapping.id}.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Lỗi khi cập nhật is_active (DB only) cho Mapping ID {mapping.id}: {e}", exc_info=True)
            flash(f"Lỗi khi cập nhật trạng thái DB của mapping: {str(e)}", "danger")

    return redirect(url_for('mappings.list_mappings'))
