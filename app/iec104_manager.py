import threading
import logging
import time
import c104 # Thư viện IEC 104 đã chọn
from app.models import IEC104Station, IEC104Point, IEC104PointType
from asyncua import ua

logger = logging.getLogger(__name__)

class IEC104Manager:
    def __init__(self, app):
        self.app = app
        self.server_instance = None
        self.server_thread = None
        self.is_running = False
        self._runtime_points = {}  # Lưu trữ {common_address: {io_address: c104_point_object}}
        self.station_config = None # Lưu cấu hình trạm đang chạy

    def _load_config_and_points(self, station_db_id):
        """Tải cấu hình trạm và các điểm IOA từ DB."""
        station = IEC104Station.query.get(station_db_id)
        if not station:
            logger.error(f"Không tìm thấy IEC104Station với ID: {station_db_id}")
            return None, {}

        self.station_config = station
        points_data = {}
        for p_db in station.points:
            try:
                # Chuyển đổi từ Enum model sang c104.Type
                point_type_c104 = getattr(c104.Type, p_db.point_type_str.name)
                points_data[p_db.io_address] = {
                    "type": point_type_c104,
                    "report_ms": p_db.report_ms,
                    "description": p_db.description
                    # Giá trị khởi tạo sẽ được set là 0 hoặc False mặc định bởi c104
                }
            except AttributeError:
                logger.error(f"Không hỗ trợ PointType '{p_db.point_type_str.name}' trong thư viện c104. Bỏ qua IOA: {p_db.io_address}")
        return station, points_data

    def _server_target(self, station_config_db, points_config):
        """Hàm mục tiêu cho luồng server."""
        try:
             # Khởi tạo server instance mà không truyền protocol_parameters ban đầu
            self.server_instance = c104.Server(
                ip=station_config_db.ip_address,
                port=station_config_db.port
            )
            logger.info(f"IEC 104 server backend tạo cho CA {station_config_db.common_address} trên {station_config_db.ip_address}:{station_config_db.port}")

            # Lấy đối tượng protocol_parameters từ server instance
            params = self.server_instance.protocol_parameters

            params = self.server_instance.protocol_parameters
                    
            # --- GÁN THAM SỐ PROTOCOL VỚI TÊN ĐÚNG ---
            params.connection_timeout = station_config_db.t0_timeout    # T0
            params.message_timeout = station_config_db.t1_timeout      # T1
            params.confirm_interval = station_config_db.t2_timeout     # T2
            params.keep_alive_interval = station_config_db.t3_timeout  # T3
            params.send_window_size = station_config_db.k_value        # K
            params.receive_window_size = station_config_db.w_value     # W
            
            logger.info(
                f"Đã áp dụng ProtocolParameters: "
                f"connection_timeout(T0)={params.connection_timeout}, "
                f"message_timeout(T1)={params.message_timeout}, "
                f"confirm_interval(T2)={params.confirm_interval}, "
                f"keep_alive_interval(T3)={params.keep_alive_interval}, "
                f"send_window_size(K)={params.send_window_size}, "
                f"receive_window_size(W)={params.receive_window_size}"
            )
            # ---------------------------------------------

            station_c104 = self.server_instance.add_station(common_address=station_config_db.common_address)
            self._runtime_points[station_config_db.common_address] = {}

            for ioa, p_conf in points_config.items():
                # point_c104 = station_c104.add_point(
                #     io_address=ioa,
                #     type=p_conf["type"],
                #     report_ms=p_conf["report_ms"] if p_conf["report_ms"] > 0 else 0 # c104 yêu cầu 0 nếu không report
                # )
                point_c104 = station_c104.add_point(
                    io_address=ioa,
                    type=p_conf["type"], # p_conf["type"] là c104.Type enum member
                    report_ms=p_conf["report_ms"] if p_conf["report_ms"] > 0 else 0
                )
                # Giá trị khởi tạo mặc định (0, False) và quality "good" được c104 xử lý
                # self._runtime_points[station_config_db.common_address][ioa] = point_c104
                self._runtime_points[station_config_db.common_address][ioa] = (point_c104, p_conf["type"]) # Lưu dưới dạng tuple

                logger.info(f"CA {station_config_db.common_address} - IOA {ioa} (Type: {p_conf['type'].name}) đã thêm vào server c104.")

            self.server_instance.start()
            self.is_running = True
            logger.info(f"IEC 104 server cho CA {station_config_db.common_address} đã khởi động.")

            # Giữ luồng chạy cho đến khi is_running là False
            while self.is_running:
                time.sleep(1)

        except Exception as e:
            logger.error(f"Lỗi trong luồng server IEC 104 (CA {station_config_db.common_address}): {e}", exc_info=True)
        finally:
            if self.server_instance:
                self.server_instance.stop()
                logger.info(f"IEC 104 server cho CA {station_config_db.common_address} đã dừng.")
            self.is_running = False
            self.server_instance = None
            self._runtime_points.pop(station_config_db.common_address, None) # Xóa runtime points của station này
            self.station_config = None


    def start_server_for_station(self, station_db_id):
        if self.is_running:
            logger.warning(f"Server IEC 104 đang chạy cho trạm CA {self.station_config.common_address if self.station_config else 'UNKNOWN'}. Hãy dừng trước khi khởi động trạm mới.")
            return False

        station_db, points_data = self._load_config_and_points(station_db_id)
        if not station_db:
            return False

        self.server_thread = threading.Thread(target=self._server_target, args=(station_db, points_data), daemon=True)
        self.server_thread.start()
        # Đợi một chút để server có thời gian khởi động và set is_running
        time.sleep(2) # Có thể cần cơ chế tốt hơn để kiểm tra trạng thái
        return self.is_running

    def stop_server(self):
        if not self.is_running:
            logger.info("Server IEC 104 chưa chạy.")
            return

        self.is_running = False # Tín hiệu cho luồng dừng lại
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=10) # Đợi luồng kết thúc
            if self.server_thread.is_alive():
                logger.warning("Luồng server IEC 104 không dừng kịp thời.")
        self.server_thread = None
        logger.info("Yêu cầu dừng server IEC 104 đã được xử lý.")


    # def update_ioa_value(self, common_address, io_address, value, quality_flags=None, timestamp_ms=None):
    #     """
    #     Cập nhật giá trị, chất lượng và timestamp cho một IOA cụ thể.
    #     quality_flags là một dict ví dụ: {'Invalid': True, 'NonTopical': False}
    #     """
    #     if not self.is_running:
    #         # logger.warning("IEC 104 server chưa chạy. Không thể cập nhật IOA.")
    #         return False

    #     station_points = self._runtime_points.get(common_address)
    #     if not station_points:
    #         logger.warning(f"Không tìm thấy runtime station với CA: {common_address}")
    #         return False

    #     point_c104 = station_points.get(io_address)
    #     if not point_c104:
    #         logger.warning(f"Không tìm thấy runtime point với CA: {common_address}, IOA: {io_address}")
    #         return False

    #     try:
    #         # Cập nhật giá trị
    #         point_c104.value = value

    #         # Cập nhật chất lượng
    #         if quality_flags:
    #             # Các cờ chất lượng trong c104.Quality là: iv, nt, sb, bl, ov
    #             # Ví dụ: quality_flags = {'iv': True, 'bl': False}
    #             q = point_c104.quality
    #             q.iv = quality_flags.get('iv', q.iv) # Invalid
    #             q.nt = quality_flags.get('nt', q.nt) # NonTopical
    #             q.sb = quality_flags.get('sb', q.sb) # Substituted
    #             q.bl = quality_flags.get('bl', q.bl) # Blocked
    #             q.ov = quality_flags.get('ov', q.ov) # Overflow

    #         # Cập nhật timestamp (phải là int, miliseconds kể từ epoch)
    #         if timestamp_ms is not None:
    #             point_c104.timestamp_ms = int(timestamp_ms)
    #         else:
    #             # Tự động đặt timestamp hiện tại nếu không được cung cấp
    #             point_c104.timestamp_ms = int(time.time() * 1000)

    #         # logger.debug(f"IEC104 CA {common_address}, IOA {io_address} updated: Value={value}, Quality={point_c104.quality}, TS={point_c104.timestamp_ms}")
    #         return True
    #     except Exception as e:
    #         logger.error(f"Lỗi khi cập nhật IOA {common_address}/{io_address}: {e}", exc_info=True)
    #         return False

    def get_status(self):
        if not self.station_config:
            return {"running": self.is_running, "station_name": "N/A", "ip_address": "N/A", "port": "N/A", "common_address": "N/A", "points_count": 0}

        return {
            "running": self.is_running,
            "station_name": self.station_config.name,
            "ip_address": self.station_config.ip_address,
            "port": self.station_config.port,
            "common_address": self.station_config.common_address,
            "points_count": len(self._runtime_points.get(self.station_config.common_address, {}))
        }

                    
    # def update_ioa_value(self, common_address, io_address, value, quality_flags=None, timestamp_ms=None):
    #             # ... (phần kiểm tra is_running, station_points, runtime_point_data giữ nguyên) ...
    #             if not self.is_running:
    #                 logger.warning(f"IEC 104 server (CA: {common_address}) chưa chạy. Không thể cập nhật IOA {io_address}.")
    #                 return False

    #             station_points = self._runtime_points.get(common_address)
    #             if not station_points:
    #                 logger.warning(f"Không tìm thấy runtime station với CA: {common_address}")
    #                 return False

    #             runtime_point_data = station_points.get(io_address)
    #             if not runtime_point_data:
    #                 logger.warning(f"Không tìm thấy runtime point data với CA: {common_address}, IOA: {io_address}")
    #                 return False

    #             point_c104, point_type_c104_enum = runtime_point_data
    #             try:
    #                 logger.info(f"IEC104 Update: CA {common_address}, IOA {io_address} (Type: {point_type_c104_enum.name}). OPC_Val='{value}'({type(value)}), OPC_QFlags={quality_flags}, OPC_TSMs={timestamp_ms}")

    #                 converted_value_for_point = None
    #                 # ... (logic chuyển đổi converted_value_for_point như cũ) ...
    #                 if point_type_c104_enum in [c104.Type.M_SP_NA_1, c104.Type.M_SP_TA_1]:
    #                     converted_value_for_point = bool(value)
    #                 elif point_type_c104_enum in [
    #                     c104.Type.M_ME_NA_1, c104.Type.M_ME_NC_1,
    #                     c104.Type.M_ME_TD_1, c104.Type.M_ME_TF_1
    #                 ]:
    #                     converted_value_for_point = float(value)
    #                 elif point_type_c104_enum in [c104.Type.M_ME_NB_1]:
    #                     converted_value_for_point = int(value)
    #                 elif point_type_c104_enum == c104.Type.M_DP_NA_1:
    #                     try:
    #                         # from asyncua import ua # Đảm bảo import
    #                         converted_value_for_point = ua.DoublePointValue(int(value))
    #                     except ValueError:
    #                         logger.error(f"Giá trị '{value}' ({type(value)}) không hợp lệ cho {point_type_c104_enum.name} tại IOA {io_address}.")
    #                         return False
    #                 else:
    #                     logger.warning(f"Không có logic chuyển đổi giá trị tường minh cho c104.Type {point_type_c104_enum.name} tại IOA {io_address}. Sẽ thử gán giá trị gốc.")
    #                     converted_value_for_point = value


    #                 # Gán giá trị Python gốc đã ép kiểu
    #                 logger.info(f"Attempting to assign to point_c104.value: {converted_value_for_point} (type: {type(converted_value_for_point)})")
    #                 point_c104.value = converted_value_for_point

    #                 # Tạo và thử gán quality
    #                 calculated_quality_bitmask = 0
    #                 if quality_flags:
    #                     if quality_flags.get('iv', False): calculated_quality_bitmask |= c104.Quality.IV.value
    #                     if quality_flags.get('nt', False): calculated_quality_bitmask |= c104.Quality.NT.value
    #                     if quality_flags.get('sb', False): calculated_quality_bitmask |= c104.Quality.SB.value
    #                     if quality_flags.get('bl', False): calculated_quality_bitmask |= c104.Quality.BL.value
    #                     if quality_flags.get('ov', False): calculated_quality_bitmask |= c104.Quality.OV.value
    #                 quality_obj_to_set = c104.Quality(value=calculated_quality_bitmask)
                    
    #                 if hasattr(point_c104, 'quality'):
    #                     try:
    #                         point_c104.quality = quality_obj_to_set
    #                         logger.info(f"Đã gán point_c104.quality cho IOA {io_address} với bitmask {calculated_quality_bitmask}")
    #                     except Exception as e_qual_set:
    #                          logger.warning(f"Lỗi khi gán point_c104.quality cho IOA {io_address}: {e_qual_set}. Chất lượng có thể không được cập nhật theo OPC UA.")
    #                 else:
    #                     logger.warning(f"Đối tượng point_c104 không có thuộc tính 'quality' cho IOA {io_address}. Chất lượng có thể không được cập nhật theo OPC UA.")

    #                 # Timestamp sẽ được thư viện c104 tự xử lý khi gửi đi,
    #                 # vì chúng ta không có cách set tường minh cho Point object.
    #                 # Chỉ log thông tin timestamp nhận từ OPC UA.
    #                 if timestamp_ms is not None:
    #                     logger.info(f"Timestamp từ OPC UA (ms since epoch): {timestamp_ms} cho IOA {io_address}. Thư viện c104 sẽ quản lý timestamp gửi đi.")
                    
    #                 logger.info(f"IEC104 CA {common_address}, IOA {io_address} (Type: {point_type_c104_enum.name}) value set to {converted_value_for_point}. Quality update attempted. Timestamp handled by library.")
    #                 return True

    #             except ValueError as ve: 
    #                 logger.error(f"Lỗi ValueError khi xử lý giá trị '{value}' ({type(value)}) cho IOA {common_address}/{io_address} (Dự kiến Type: {point_type_c104_enum.name}): {ve}", exc_info=True)
    #                 return False
    #             except TypeError as te: 
    #                 logger.error(f"Lỗi TypeError khi gán giá trị cho IOA {common_address}/{io_address} (Value: {converted_value_for_point if 'converted_value_for_point' in locals() else 'UNKNOWN'}, Type: {type(converted_value_for_point) if 'converted_value_for_point' in locals() else 'UNKNOWN'}): {te}", exc_info=True)
    #                 return False
    #             except Exception as e:
    #                 logger.error(f"Lỗi khi cập nhật IOA {common_address}/{io_address} (Type: {point_type_c104_enum.name}): {e}", exc_info=True)
    #                 return False

    def update_ioa_value(self, common_address, io_address, value, quality_flags=None, timestamp_ms=None):
                if not self.is_running:
                    logger.warning(f"IEC 104 server (CA: {common_address}) chưa chạy. Không thể cập nhật IOA {io_address}.")
                    return False

                station_points = self._runtime_points.get(common_address)
                if not station_points:
                    logger.warning(f"Không tìm thấy runtime station với CA: {common_address}")
                    return False

                runtime_point_data = station_points.get(io_address)
                if not runtime_point_data:
                    logger.warning(f"Không tìm thấy runtime point data với CA: {common_address}, IOA: {io_address}")
                    return False

                point_c104, point_type_c104_enum = runtime_point_data
                
                # Log thông tin nhận được
                logger.info(f"IEC104 Update: CA {common_address}, IOA {io_address} (TargetType: {point_type_c104_enum.name}). "
                            f"OPC_Val='{value}'({type(value)}), OPC_QFlags={quality_flags}, OPC_TSMs={timestamp_ms}")

                try:
                    # 1. Chuẩn bị Quality Object
                    calculated_quality_bitmask = 0
                    if quality_flags:
                        if quality_flags.get('iv', False): calculated_quality_bitmask |= c104.Quality.IV.value
                        if quality_flags.get('nt', False): calculated_quality_bitmask |= c104.Quality.NT.value
                        if quality_flags.get('sb', False): calculated_quality_bitmask |= c104.Quality.SB.value
                        if quality_flags.get('bl', False): calculated_quality_bitmask |= c104.Quality.BL.value
                        if quality_flags.get('ov', False): calculated_quality_bitmask |= c104.Quality.OV.value
                    quality_obj_for_point = c104.Quality(value=calculated_quality_bitmask)

                    # 2. Chuẩn bị Timestamp (sẽ là None vì c104.Timestamp không tạo được)
                    timestamp_param_for_constructor = None
                    if timestamp_ms is not None:
                        logger.debug(f"IOA {io_address}: Nhận timestamp_ms={timestamp_ms}. Sẽ truyền None cho InfoObj constructor.")
                    
                    # 3. Chuẩn bị giá trị và tạo Information Object tương ứng
                    information_object_instance = None
                    converted_value_for_point = None # Giá trị Python gốc để gán cho point_c104.value

                    # ---- Xử lý các kiểu dữ liệu IEC 104 ----
                    # Đo lường (Measured Values)
                    if point_type_c104_enum == c104.Type.M_ME_NA_1: # Normalized, No Time
                        converted_value_for_point = float(value)
                        information_object_instance = c104.NormalizedInfo(actual=converted_value_for_point, quality=quality_obj_for_point)
                    elif point_type_c104_enum == c104.Type.M_ME_TD_1: # Normalized, With Time
                        converted_value_for_point = float(value)
                        information_object_instance = c104.NormalizedInfo(actual=converted_value_for_point, quality=quality_obj_for_point, recorded_at=timestamp_param_for_constructor)
                    
                    elif point_type_c104_enum == c104.Type.M_ME_NB_1: # Scaled, No Time
                        converted_value_for_point = int(value)
                        information_object_instance = c104.ScaledInfo(actual=converted_value_for_point, quality=quality_obj_for_point)
                    elif point_type_c104_enum == c104.Type.M_ME_TE_1: # Scaled, With Time (Kiểm tra tên lớp Info nếu có)
                        converted_value_for_point = int(value)
                        # Giả sử ScaledInfo cũng có recorded_at, cần kiểm tra signature nếu có lỗi
                        information_object_instance = c104.ScaledInfo(actual=converted_value_for_point, quality=quality_obj_for_point, recorded_at=timestamp_param_for_constructor)

                    elif point_type_c104_enum == c104.Type.M_ME_NC_1: # ShortFloat, No Time
                        converted_value_for_point = float(value)
                        information_object_instance = c104.ShortInfo(actual=converted_value_for_point, quality=quality_obj_for_point)
                    elif point_type_c104_enum == c104.Type.M_ME_TF_1: # ShortFloat, With Time
                        converted_value_for_point = float(value)
                        information_object_instance = c104.ShortInfo(actual=converted_value_for_point, quality=quality_obj_for_point, recorded_at=timestamp_param_for_constructor)

                    # Thông tin điểm đơn (Single Point Information)
                    elif point_type_c104_enum == c104.Type.M_SP_NA_1: # Boolean, No Time
                        converted_value_for_point = bool(value)
                        information_object_instance = c104.SingleInfo(value=converted_value_for_point, quality=quality_obj_for_point)
                    elif point_type_c104_enum == c104.Type.M_SP_TA_1: # Boolean, With Time
                        converted_value_for_point = bool(value)
                        information_object_instance = c104.SingleInfo(value=converted_value_for_point, quality=quality_obj_for_point, recorded_at=timestamp_param_for_constructor) # Giả sử SingleInfo có recorded_at

                    # Thông tin điểm kép (Double Point Information)
                    elif point_type_c104_enum == c104.Type.M_DP_NA_1: # Double Point, No Time
                        try:
                            dp_val_enum = ua.DoublePointValue(int(value)) # 0:intermediate, 1:OFF, 2:ON, 3:intermediate
                            converted_value_for_point = dp_val_enum # Giá trị cho point.value có thể cần là int hoặc bool tùy thư viện
                            information_object_instance = c104.DoubleInfo(value=dp_val_enum, quality=quality_obj_for_point)
                        except ValueError:
                            logger.error(f"Giá trị '{value}' ({type(value)}) không hợp lệ cho M_DP_NA_1 (cần 0-3) tại IOA {io_address}.")
                            return False
                    elif point_type_c104_enum == c104.Type.M_DP_TA_1: # Double Point, With Time
                        try:
                            dp_val_enum = ua.DoublePointValue(int(value))
                            converted_value_for_point = dp_val_enum
                            information_object_instance = c104.DoubleInfo(value=dp_val_enum, quality=quality_obj_for_point, recorded_at=timestamp_param_for_constructor)
                        except ValueError:
                            logger.error(f"Giá trị '{value}' ({type(value)}) không hợp lệ cho M_DP_TA_1 (cần 0-3) tại IOA {io_address}.")
                            return False
                    
                    # Giá trị đếm (Integrated Totals / Counter)
                    elif point_type_c104_enum == c104.Type.M_IT_NA_1: # Counter, No Time
                        converted_value_for_point = int(value)
                        # Giả sử BinaryCounterInfo là lớp đúng và có signature này
                        information_object_instance = c104.BinaryCounterInfo(value=converted_value_for_point, quality=quality_obj_for_point)
                    elif point_type_c104_enum == c104.Type.M_IT_TA_1: # Counter, With Time
                        converted_value_for_point = int(value)
                        information_object_instance = c104.BinaryCounterInfo(value=converted_value_for_point, quality=quality_obj_for_point, recorded_at=timestamp_param_for_constructor)

                    # Bổ sung các kiểu khác nếu cần (Step position, Bitstring, Commands, etc.)

                    else:
                        logger.error(f"Không hỗ trợ tạo Information Object hoặc gán giá trị cho c104.Type {point_type_c104_enum.name} tại IOA {io_address}.")
                        return False

                    # 4. Gán giá trị và quality cho point_c104
                    if information_object_instance and converted_value_for_point is not None:
                        # Dựa trên lỗi TypeError trước, point_c104.value mong đợi giá trị Python gốc
                        logger.info(f"Attempting to assign to point_c104.value: {converted_value_for_point} (type: {type(converted_value_for_point)}) for IOA {io_address}")
                        point_c104.value = converted_value_for_point
                        
                        # Thử gán quality
                        if hasattr(point_c104, 'quality'):
                            try:
                                point_c104.quality = quality_obj_for_point
                                logger.info(f"Đã gán point_c104.quality cho IOA {io_address} với bitmask {calculated_quality_bitmask}")
                            except Exception as e_qual_set:
                                 logger.warning(f"Lỗi khi gán point_c104.quality cho IOA {io_address}: {e_qual_set}. Chất lượng có thể không được cập nhật theo OPC UA.")
                        else:
                            logger.warning(f"Đối tượng point_c104 không có thuộc tính 'quality' cho IOA {io_address}.")
                        
                        # Timestamp được thư viện tự xử lý
                        if timestamp_ms is not None:
                             logger.info(f"Timestamp từ OPC UA (ms): {timestamp_ms} cho IOA {io_address}. Thư viện c104 sẽ quản lý timestamp gửi đi.")

                        logger.info(f"IEC104 CA {common_address}, IOA {io_address} (Type: {point_type_c104_enum.name}) value set to {converted_value_for_point}. Quality update attempted. Timestamp handled by library.")
                        return True
                    else:
                        logger.error(f"Không tạo được information_object_instance hoặc converted_value_for_point là None cho IOA {io_address} (Type: {point_type_c104_enum.name}).")
                        return False

                except ValueError as ve: 
                    logger.error(f"Lỗi ValueError khi xử lý/ép kiểu giá trị '{value}' ({type(value)}) cho IOA {common_address}/{io_address} (TargetType: {point_type_c104_enum.name}): {ve}", exc_info=True)
                    return False
                except TypeError as te: 
                    logger.error(f"Lỗi TypeError khi tạo InfoObj hoặc gán giá trị cho IOA {common_address}/{io_address} (Value: {converted_value_for_point if 'converted_value_for_point' in locals() else 'UNKNOWN'}, TargetType: {point_type_c104_enum.name}): {te}", exc_info=True)
                    return False
                except Exception as e:
                    logger.error(f"Lỗi chung khi cập nhật IOA {common_address}/{io_address} (Type: {point_type_c104_enum.name}): {e}", exc_info=True)
                    return False