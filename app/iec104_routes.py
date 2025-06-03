from flask import Blueprint, render_template, redirect, url_for, flash, request
from app import db, iec104_manager # Đảm bảo iec104_manager được import đúng
from .models import IEC104Station, IEC104Point, IEC104PointType
from .iec104_forms import IEC104StationForm, IEC104PointForm

iec104_bp = Blueprint('iec104_bp', __name__)

@iec104_bp.route('/')
def station_config_overview():
    stations = IEC104Station.query.order_by(IEC104Station.id).all()
    server_status = iec104_manager.get_status() if iec104_manager else None
    # Nếu không có trạm nào và muốn hiển thị form thêm trạm mới ngay
    if not stations and not request.args.get('station_id'):
         return redirect(url_for('iec104_bp.configure_station'))
    return render_template('iec104/station_config.html', stations=stations, server_status=server_status, station_form=None, station=None)

@iec104_bp.route('/station/configure', methods=['GET', 'POST'])
@iec104_bp.route('/station/configure/<int:station_id>', methods=['GET', 'POST'])
def configure_station(station_id=None):
    station = None
    if station_id:
        station = IEC104Station.query.get_or_404(station_id)

    form = IEC104StationForm(obj=station)
    stations_list = IEC104Station.query.order_by(IEC104Station.id).all() # Để hiển thị danh sách bên dưới
    server_status = iec104_manager.get_status() if iec104_manager else None


    if form.validate_on_submit():
        try:
            if station: # Edit
                # Kiểm tra CA nếu thay đổi có bị trùng không (trừ chính nó)
                existing_ca = IEC104Station.query.filter(IEC104Station.common_address == form.common_address.data, IEC104Station.id != station.id).first()
                if existing_ca:
                    flash('Common Address đã tồn tại cho một trạm khác.', 'danger')
                    return render_template('iec104/station_config.html', station_form=form, station=station, stations=stations_list, server_status=server_status)

                form.populate_obj(station)
                flash('Cấu hình trạm đã được cập nhật!', 'success')
            else: # Add new
                existing_ca = IEC104Station.query.filter_by(common_address=form.common_address.data).first()
                if existing_ca:
                    flash('Common Address đã tồn tại.', 'danger')
                    return render_template('iec104/station_config.html', station_form=form, station=None, stations=stations_list, server_status=server_status)

                new_station = IEC104Station()
                form.populate_obj(new_station)
                db.session.add(new_station)
                flash('Trạm IEC 104 mới đã được thêm!', 'success')

            db.session.commit()
            # Nếu server đang chạy với cấu hình cũ của trạm này, cần thông báo để người dùng restart
            if iec104_manager and iec104_manager.is_running and iec104_manager.station_config and iec104_manager.station_config.id == (station.id if station else new_station.id):
                flash('Cấu hình trạm đã thay đổi. Vui lòng dừng và khởi động lại server IEC 104 để áp dụng.', 'warning')

            return redirect(url_for('iec104_bp.station_config_overview'))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi lưu cấu hình trạm: {str(e)}', 'danger')

    return render_template('iec104/station_config.html', station_form=form, station=station, stations=stations_list, server_status=server_status)


@iec104_bp.route('/station/<int:station_id>/points', methods=['GET', 'POST'])
@iec104_bp.route('/station/<int:station_id>/points/<int:point_id>', methods=['GET', 'POST']) # For editing a point
def manage_points(station_id, point_id=None):
    station = IEC104Station.query.get_or_404(station_id)
    point_to_edit = None
    if point_id:
        point_to_edit = IEC104Point.query.filter_by(id=point_id, station_id=station.id).first_or_404()

    form = IEC104PointForm(obj=point_to_edit)
    # Đảm bảo SelectField hiển thị đúng giá trị hiện tại khi edit
    if point_to_edit and request.method == 'GET':
         form.point_type_str.data = point_to_edit.point_type_str.name


    if form.validate_on_submit():
        try:
            ioa_to_check = form.io_address.data
            # Kiểm tra IOA đã tồn tại cho trạm này chưa (trừ chính nó nếu đang edit)
            query = IEC104Point.query.filter_by(station_id=station.id, io_address=ioa_to_check)
            if point_to_edit:
                query = query.filter(IEC104Point.id != point_to_edit.id)
            existing_point = query.first()

            if existing_point:
                flash(f'Địa chỉ IOA {ioa_to_check} đã tồn tại cho trạm này.', 'danger')
            else:
                if point_to_edit: # Edit
                    point_to_edit.io_address = form.io_address.data
                    point_to_edit.description = form.description.data
                    point_to_edit.point_type_str = IEC104PointType[form.point_type_str.data]
                    point_to_edit.report_ms = form.report_ms.data
                    flash('Điểm dữ liệu IOA đã được cập nhật!', 'success')
                else: # Add new
                    new_point = IEC104Point(
                        station_id=station.id,
                        io_address=form.io_address.data,
                        description=form.description.data,
                        point_type_str=IEC104PointType[form.point_type_str.data], # Chuyển từ string name sang Enum member
                        report_ms=form.report_ms.data
                    )
                    db.session.add(new_point)
                    flash('Điểm dữ liệu IOA mới đã được thêm!', 'success')

                db.session.commit()
                # Nếu server đang chạy với cấu hình cũ của trạm này, cần thông báo
                if iec104_manager and iec104_manager.is_running and iec104_manager.station_config and iec104_manager.station_config.id == station.id:
                    flash('Danh sách IOA đã thay đổi. Vui lòng dừng và khởi động lại server IEC 104 để áp dụng thay đổi IOA.', 'warning')
                return redirect(url_for('iec104_bp.manage_points', station_id=station.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi lưu điểm dữ liệu IOA: {str(e)}', 'danger')

    points = IEC104Point.query.filter_by(station_id=station.id).order_by(IEC104Point.io_address).all()
    return render_template('iec104/manage_points.html', station=station, points=points, point_form=form, point_to_edit=point_to_edit)

@iec104_bp.route('/station/<int:station_id>/points/<int:point_id>/delete', methods=['POST'])
def delete_point(station_id, point_id):
    point = IEC104Point.query.filter_by(id=point_id, station_id=station_id).first_or_404()
    try:
        db.session.delete(point)
        db.session.commit()
        flash('Điểm dữ liệu IOA đã được xóa.', 'success')
        if iec104_manager and iec104_manager.is_running and iec104_manager.station_config and iec104_manager.station_config.id == station_id:
            flash('Danh sách IOA đã thay đổi. Vui lòng dừng và khởi động lại server IEC 104.', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi khi xóa điểm dữ liệu IOA: {str(e)}', 'danger')
    return redirect(url_for('iec104_bp.manage_points', station_id=station_id))

@iec104_bp.route('/server/start', methods=['POST'])
def start_iec_server():
    if not iec104_manager:
        flash('IEC104 Manager chưa được khởi tạo.', 'danger')
        return redirect(url_for('iec104_bp.station_config_overview'))

    if iec104_manager.is_running:
        flash('Server IEC 104 đã chạy rồi.', 'warning')
        return redirect(url_for('iec104_bp.station_config_overview'))

    station_id_to_start = request.form.get('station_id_to_start', type=int)
    if not station_id_to_start:
        flash('Vui lòng chọn một trạm để khởi động.', 'danger')
        return redirect(url_for('iec104_bp.station_config_overview'))

    station_to_run = IEC104Station.query.get(station_id_to_start)
    if not station_to_run:
        flash(f'Không tìm thấy trạm với ID {station_id_to_start}.', 'danger')
        return redirect(url_for('iec104_bp.station_config_overview'))

    if not station_to_run.points.first(): # Kiểm tra xem trạm có ít nhất 1 IOA không
        flash(f'Trạm "{station_to_run.name}" (CA: {station_to_run.common_address}) chưa có IOA nào được cấu hình. Không thể khởi động.', 'warning')
        return redirect(url_for('iec104_bp.station_config_overview'))

    success = iec104_manager.start_server_for_station(station_id_to_start)
    if success:
        flash(f'Server IEC 104 cho trạm "{station_to_run.name}" (CA: {station_to_run.common_address}) đang khởi động...', 'success')
    else:
        flash(f'Không thể khởi động server IEC 104 cho trạm "{station_to_run.name}". Xem log để biết chi tiết.', 'danger')
    return redirect(url_for('iec104_bp.station_config_overview'))

@iec104_bp.route('/server/stop', methods=['POST'])
def stop_iec_server():
    if not iec104_manager:
        flash('IEC104 Manager chưa được khởi tạo.', 'danger')
        return redirect(url_for('iec104_bp.station_config_overview'))

    if not iec104_manager.is_running:
        flash('Server IEC 104 chưa chạy.', 'warning')
    else:
        iec104_manager.stop_server()
        flash('Server IEC 104 đã được yêu cầu dừng.', 'info')
    return redirect(url_for('iec104_bp.station_config_overview'))