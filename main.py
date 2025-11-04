import queue
import sys
import threading
from datetime import datetime
from serial_reader import read_list_ports, open_serial_port, read_from_com

from PyQt6.QtWidgets import QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView, QComboBox, QAbstractItemView
from PyQt6.QtGui import QColor
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QObject
import struct
from designe import Ui_MainWindow  
from decode import Frame
import serial


class ScanSignals(QObject):
    """Сигналы для обновления UI из потока сканирования"""
    status_update = pyqtSignal(str)
    button_update = pyqtSignal(str)
    scan_finished = pyqtSignal(bool, str, str, str, str)  # success, baudrate, bytesize, parity, stopbit


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)  # Настройка UI из сгенерированного файла
        
        # Инициализация переменных
        self.serial_port = None
        self.read_thread = None
        self.message_queue = queue.Queue(maxsize=0)  # Очередь для сырых hex строк из COM порта (неограниченная)
        self.decoded_queue = queue.Queue(maxsize=0)  # Очередь для декодированных сообщений (frame, message_bytes) (неограниченная)
        self.decode_thread = None
        self.is_connected = False
        self.message_counter = 0
        # Индексы для обновления строк
        self.request_index_by_bytes = {}
        self.response_index_by_signature = {}
        self.last_request_time_by_af = {}  # Время последнего запроса по (address, function)
        self.last_request_time_by_key = {}  # Время последнего запроса по req_key (для функций 5 и 6)
        self.last_request_row_by_af = {}
        self.skip_first_invalid_crc = False
        self.connected_at = None
        # Ответы, ожидающие своих запросов: ключ = (address, base_function), значение = список (row_data, frame, message_bytes)
        self.pending_responses = {}
        self.last_message_time = None
        self.process_pending_timer = None
        # Счетчики сообщений: ключ = уникальный идентификатор сообщения, значение = количество раз
        self.message_counters = {}
        # Хранилище данных сообщений: ключ = row_position, значение = (data_bytes, frame)
        self.message_data_storage = {}
        # Типы данных для регистров: ключ = row_position, значение = список типов для каждого регистра
        self.register_types_storage = {}
        
        # Флаг начала вывода: True = ждем первого запроса, False = выводим все сообщения
        self.waiting_for_first_request = True
        
        # Отслеживание сообщений функций 5 и 6 для определения запрос/ответ
        # Ключ = (address, function, data_bytes), значение = количество раз
        self.write_single_message_count = {}
        
        # Заполняем comboBox_COM при запуске
        self.populate_com_ports()
        # Значение по умолчанию: Биты данных = 8
        try:
            self.comboBox_date_bit.setCurrentText("8")
        except Exception:
            pass

        # Подключаем кнопку Подключить к функции подключения к COM-порту
        self.pushButton_connect.clicked.connect(self.on_connect_clicked)
        
        # Подключаем кнопку сканирования сети
        self.pushButton_scan.clicked.connect(self.on_scan_clicked)
        
        # Флаг для остановки сканирования
        self.scanning_active = False
        self.scan_thread = None
        self.current_test_port = None  # Текущий тестовый порт для возможности закрытия при остановке
        self.scan_successful = False  # Флаг успешного сканирования
        # Сигналы для обновления UI из потока сканирования
        self.scan_signals = ScanSignals()
        self.scan_signals.status_update.connect(self.update_scan_status_label)
        self.scan_signals.button_update.connect(self.update_scan_button)
        self.scan_signals.scan_finished.connect(self.on_scan_finished)
        
        # Таймер для обработки очереди декодированных сообщений
        self.timer = QTimer()
        self.timer.timeout.connect(self.process_decoded_messages)
        self.timer.start(50)  # Проверка каждые 50 мс для более быстрой обработки
        
        # Запускаем поток декодирования сразу при старте (будет ждать подключения)
        self.decode_thread = threading.Thread(
            target=self.decode_messages,
            daemon=True
        )
        self.decode_thread.start()
        
        # Таймер для обработки ожидающих ответов (проверка каждые 500 мс)
        self.process_pending_timer = QTimer()
        self.process_pending_timer.timeout.connect(self.process_pending_responses)
        self.process_pending_timer.start(500)
        
        # Подключаем фильтры
        self.checkBox_filter_crc_ok.stateChanged.connect(self.apply_filters)
        self.checkBox_filter_errors_only.stateChanged.connect(self.apply_filters)
        self.comboBox_filter_address.currentTextChanged.connect(self.apply_filters)
        self.comboBox_filter_function.currentTextChanged.connect(self.apply_filters)
        
        # Инициализируем списки для фильтров адреса и функции
        self.comboBox_filter_address.addItem("")
        self.comboBox_filter_function.addItem("")
        
        # Подключаем кнопку очистки
        self.pushButton_clear.clicked.connect(self.clear_table)
        
        # Подключаем кнопку сброса фильтров
        self.pushButton_reset_filters.clicked.connect(self.reset_all_filters)
        
        # Подключаем обработчик выбора строки в таблице
        self.SnifferTable.itemSelectionChanged.connect(self.on_row_selected)
        
        # Настраиваем таблицу "Значения"
        values_header = self.ValuesTable.horizontalHeader()
        values_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        values_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        values_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        # Настройки таблицы: видимые заголовки всегда и корректная ширина колонок
        header = self.SnifferTable.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionsClickable(False)
        header.setStretchLastSection(True)
        # По умолчанию тянем все секции по ширине окна
        for i in range(self.SnifferTable.columnCount()):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        # Для колонки "Количество регистров/байт" (колонка 6) устанавливаем минимальную ширину для читаемости ошибок
        header.setMinimumSectionSize(200)
        # Устанавливаем отдельную минимальную ширину для колонки 6
        header.resizeSection(6, max(200, header.sectionSize(6)))
        # Включаем перенос текста для ячеек (особенно для сообщений об ошибках)
        self.SnifferTable.setWordWrap(True)
        # Включаем автоматическое изменение высоты строки для переноса текста
        self.SnifferTable.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        
        # Настройка выделения строки
        self.SnifferTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.SnifferTable.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Устанавливаем стартовые/минимальные ширины по длине заголовков
        fm = self.SnifferTable.fontMetrics()
        for i in range(self.SnifferTable.columnCount()):
            text = self.SnifferTable.horizontalHeaderItem(i).text() if self.SnifferTable.horizontalHeaderItem(i) else ""
            min_w = max(80, fm.horizontalAdvance(text) + 24)  # небольшой отступ
            header.resizeSection(i, min_w)
        header.setMinimumSectionSize(60)

        # Конфигурируем высоту верхнего дока и стартовое распределение места
        try:
            self.dockWidget_Panel_connect.setMinimumHeight(150)
            self.dockWidget_Panel_connect.setMaximumHeight(16777215)
            self.dockWidget_Sniffer.setMinimumHeight(200)
            self.dockWidget_Values.setMinimumHeight(200)
            # Первичное распределение: панель ~150px, остальное — сниффер и значения
            self.resizeDocks(
                [self.dockWidget_Panel_connect, self.dockWidget_Sniffer],
                [150, max(300, self.height() - 150)],
                Qt.Orientation.Vertical,
            )
            # Размещаем Сниффер и Значения рядом горизонтально на одном уровне
            # Сниффер слева (широкий), Значения справа (уже)
            # Используем splitDockWidget, который уже был вызван в designe.py
            # Используем пропорциональное распределение для адаптивности (70% / 30%)
            # Ширина будет автоматически масштабироваться благодаря Expanding политике
            # Используем полную ширину окна без отступов для выравнивания с панелью подключения
            available_width = self.width()
            sniffer_width = int(available_width * 0.7)  # 70% для Сниффера
            values_width = int(available_width * 0.3)  # 30% для Значений
            self.resizeDocks(
                [self.dockWidget_Sniffer, self.dockWidget_Values],
                [sniffer_width, values_width],
                Qt.Orientation.Horizontal,
            )
        except Exception:
            pass
        
        # Подключаем обработчик изменения размера окна для адаптивности
        self.resizeEvent = self.on_resize_event
    
    def on_resize_event(self, event):
        """Обработчик изменения размера окна - адаптивно масштабирует dock widgets"""
        super().resizeEvent(event)
        try:
            # Пересчитываем ширину для Сниффер и Значения при изменении размера окна
            # Используем полную ширину окна без отступов для выравнивания с панелью подключения
            available_width = self.width()
            sniffer_width = int(available_width * 0.7)  # 70% для Сниффера
            values_width = int(available_width * 0.3)  # 30% для Значений
            self.resizeDocks(
                [self.dockWidget_Sniffer, self.dockWidget_Values],
                [sniffer_width, values_width],
                Qt.Orientation.Horizontal,
            )
        except Exception:
            pass

    def populate_com_ports(self):
        """
        Заполняет comboBox_COM списком доступных COM-портов
        """
        try:
            # Получаем список портов
            ports = read_list_ports()
            self.comboBox_COM.clear()  # Очищаем ComboBox
            if ports:
                self.comboBox_COM.addItems(ports)  # Добавляем найденные порты
        except (ValueError, Exception):
            # Если порты не найдены, просто оставляем ComboBox пустым
            # Ошибка будет показана только при попытке подключения
            self.comboBox_COM.clear()

    def convert_parity(self, parity_text):
        """Преобразует текст четности в константу serial"""
        parity_map = {
            "Нет": serial.PARITY_NONE,
            "Четный": serial.PARITY_EVEN,
            "Нечетный": serial.PARITY_ODD
        }
        return parity_map.get(parity_text, serial.PARITY_NONE)
    
    def convert_stopbits(self, stop_bit_text):
        """Преобразует текст стоп-битов в значение"""
        stopbits_map = {
            "1": serial.STOPBITS_ONE,
            "2": serial.STOPBITS_TWO
        }
        return stopbits_map.get(stop_bit_text, serial.STOPBITS_ONE)
    
    def on_scan_clicked(self):
        """Обработка нажатия кнопки сканирования сети"""
        if self.scanning_active:
            # Остановка сканирования
            self.scanning_active = False
            # Закрываем текущий тестовый порт, если он открыт
            if self.current_test_port:
                try:
                    self.current_test_port.close()
                except:
                    pass
                self.current_test_port = None
            self.pushButton_scan.setText("Сканирование сети")
            self.label_scan_status.setText("")
            return
        
        # Проверяем наличие COM порта
        com_port = self.comboBox_COM.currentText()
        if not com_port:
            QMessageBox.warning(self, "Ошибка", "Выберите COM-порт")
            return
        
        # Если уже подключены - отключаемся
        if self.is_connected:
            self.on_connect_clicked()
        
        # Запускаем сканирование в отдельном потоке
        self.scanning_active = True
        self.scan_successful = False  # Сбрасываем флаг успешного сканирования
        self.pushButton_scan.setText("Остановить сканирование")
        # Показываем label статуса
        self.label_scan_status.setVisible(True)
        self.label_scan_status.setText("Начало сканирования...")
        self.scan_thread = threading.Thread(
            target=self.scan_network_parameters,
            args=(com_port,),
            daemon=True
        )
        self.scan_thread.start()
    
    def update_scan_status_label(self, text):
        """Обновляет текст статуса сканирования (вызывается из сигнала)"""
        self.label_scan_status.setText(text)
        self.label_scan_status.setVisible(True)
    
    def update_scan_button(self, text):
        """Обновляет текст кнопки сканирования (вызывается из сигнала)"""
        self.pushButton_scan.setText(text)
    
    def on_scan_finished(self, success, baudrate, bytesize, parity, stopbit):
        """Обработка завершения сканирования (вызывается из сигнала)"""
        if success:
            # Устанавливаем найденные параметры
            found_index = -1
            for i in range(self.comboBox_baudrate.count()):
                if self.comboBox_baudrate.itemText(i) == baudrate:
                    found_index = i
                    break
            if found_index >= 0:
                self.comboBox_baudrate.setCurrentIndex(found_index)
            else:
                self.comboBox_baudrate.addItem(baudrate)
                self.comboBox_baudrate.setCurrentText(baudrate)
            
            self.comboBox_date_bit.setCurrentText(bytesize)
            self.comboBox_parity.setCurrentText(parity)
            self.comboBox_stop_bit.setCurrentText(stopbit)
            
            # Устанавливаем флаг успешного сканирования
            self.scan_successful = True
            # Автоматически подключаемся через 500 мс
            QTimer.singleShot(500, self.on_connect_clicked)
        else:
            # Сканирование не удалось
            self.scanning_active = False
            self.pushButton_scan.setText("Сканирование сети")
            self.label_scan_status.setText("")
            QMessageBox.warning(self, "Сканирование", "Не обнаружен обмен")
    
    def format_parity_short(self, parity_text):
        """Преобразует текст четности в короткий формат"""
        parity_map = {
            "Нет": "N",
            "Четный": "E",
            "Нечетный": "O"
        }
        return parity_map.get(parity_text, "N")
    
    def update_scan_status(self, baudrate, bytesize, parity_text, stop_bit_text):
        """Обновляет статус сканирования в UI через сигнал"""
        parity_short = self.format_parity_short(parity_text)
        status_text = f"Подключение {baudrate}-{bytesize}-{parity_short}-{stop_bit_text}"
        # Используем сигнал для безопасного обновления из потока
        self.scan_signals.status_update.emit(status_text)
    
    def scan_network_parameters(self, com_port):
        """Сканирует параметры сети для указанного COM порта"""
        # Все возможные комбинации параметров
        # Используем стандартные скорости Modbus
        baudrates = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
        bytesizes = [7, 8]
        parities = ["Нет", "Четный", "Нечетный"]
        stopbits_list = ["1", "2"]
        
        # Перебираем все комбинации, начиная со скорости
        for baudrate in baudrates:
            if not self.scanning_active:
                break
            
            for bytesize in bytesizes:
                if not self.scanning_active:
                    break
                
                for parity_text in parities:
                    if not self.scanning_active:
                        break
                    
                    for stop_bit_text in stopbits_list:
                        if not self.scanning_active:
                            break
                        
                        # Преобразуем параметры
                        parity = self.convert_parity(parity_text)
                        stopbits = self.convert_stopbits(stop_bit_text)
                        
                        # Обновляем статус сканирования через сигнал
                        self.update_scan_status(baudrate, bytesize, parity_text, stop_bit_text)
                        # Небольшая пауза для обработки события обновления UI
                        import time
                        time.sleep(0.05)
                        
                        # Пытаемся подключиться с этими параметрами
                        test_port = None
                        try:
                            test_port = serial.Serial(
                                port=com_port,
                                baudrate=baudrate,
                                bytesize=bytesize,
                                parity=parity,
                                stopbits=stopbits,
                                timeout=2.0  # Таймаут для чтения
                            )
                            
                            # Сохраняем ссылку на порт для возможности закрытия при остановке
                            self.current_test_port = test_port
                            
                            # Очищаем буфер порта
                            test_port.reset_input_buffer()
                            
                            # Читаем сообщения в течение 2 секунд
                            valid_messages_count = 0
                            total_messages_count = 0
                            start_time = datetime.now()
                            test_queue = queue.Queue()
                            
                            # Запускаем поток чтения
                            test_read_thread = threading.Thread(
                                target=read_from_com,
                                args=(test_port, test_queue),
                                daemon=True
                            )
                            test_read_thread.start()
                            
                            # Проверяем сообщения в течение 2 секунд
                            while (datetime.now() - start_time).total_seconds() < 2.0:
                                if not self.scanning_active:
                                    break
                                
                                try:
                                    message_hex = test_queue.get(timeout=0.1)
                                    message_bytes = bytes.fromhex(message_hex)
                                    if len(message_bytes) >= 4:
                                        total_messages_count += 1
                                        frame = Frame(message_bytes)
                                        if frame.CRC_ok:
                                            valid_messages_count += 1
                                except queue.Empty:
                                    continue
                                except Exception:
                                    pass
                            
                            # После завершения 2 секунд проверяем процент валидных сообщений
                            if total_messages_count > 0:
                                valid_percentage = (valid_messages_count / total_messages_count) * 100
                                # Если процент валидных сообщений >= 80% - параметры найдены
                                if valid_percentage >= 80.0:
                                    # Закрываем тестовый порт (поток чтения закроется автоматически)
                                    try:
                                        test_port.close()
                                    except:
                                        pass
                                    self.current_test_port = None
                                    
                                    # Останавливаем сканирование
                                    self.scanning_active = False
                                    
                                    # Отправляем сигналы о успешном завершении с найденными параметрами
                                    self.scan_signals.status_update.emit("Параметры найдены!")
                                    self.scan_signals.button_update.emit("Сканирование сети")
                                    self.scan_signals.scan_finished.emit(
                                        True,  # success
                                        str(baudrate),
                                        str(bytesize),
                                        parity_text,
                                        stop_bit_text
                                    )
                                    
                                    return
                            
                            # Закрываем тестовый порт (поток чтения закроется автоматически)
                            try:
                                test_port.close()
                            except:
                                pass
                            self.current_test_port = None
                            
                        except Exception:
                            # Ошибка подключения с этими параметрами - пробуем следующие
                            if test_port:
                                try:
                                    test_port.close()
                                except:
                                    pass
                            self.current_test_port = None
                            continue
        
        # Если дошли сюда - правильные параметры не найдены
        if self.scanning_active:
            self.scanning_active = False
            # Отправляем сигнал о неудачном завершении
            self.scan_signals.status_update.emit("")
            self.scan_signals.button_update.emit("Сканирование сети")
            self.scan_signals.scan_finished.emit(False, "", "", "", "")

    def on_connect_clicked(self):
        """Обработка нажатия кнопки подключения"""
        # Если это автоматическое подключение после сканирования, проверяем флаг
        if hasattr(self, 'scan_successful') and self.scan_successful:
            # Сбрасываем флаг после использования
            self.scan_successful = False
        
        # Закрываем тестовый порт сканирования, если он открыт
        if self.current_test_port:
            try:
                if self.current_test_port.is_open:
                    self.current_test_port.close()
            except:
                pass
            self.current_test_port = None
        
        # Закрываем существующий порт, если он открыт
        if self.serial_port:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except:
                pass
            self.serial_port = None
        
        # Небольшая задержка для освобождения порта системой
        import time
        time.sleep(0.2)
        
        if not self.is_connected:
            # Подключение
            try:
                # Проверяем наличие доступных портов
                try:
                    available_ports = read_list_ports()
                    if not available_ports:
                        QMessageBox.warning(self, "Ошибка", "COM порты не найдены")
                        return
                except ValueError as e:
                    QMessageBox.warning(self, "Ошибка", f"COM порты не найдены: {str(e)}")
                    return
                
                # Получаем значения из всех ComboBox
                com_port = self.comboBox_COM.currentText()
                if not com_port:
                    QMessageBox.warning(self, "Ошибка", "Выберите COM-порт")
                    return
                
                baud_rate = int(self.comboBox_baudrate.currentText())
                bytesize = int(self.comboBox_date_bit.currentText())
                parity_text = self.comboBox_parity.currentText()
                stop_bit_text = self.comboBox_stop_bit.currentText()
                
                parity = self.convert_parity(parity_text)
                stopbits = self.convert_stopbits(stop_bit_text)
                
                # Проверяем, что все параметры получены корректно
                if not com_port or not baud_rate or not bytesize or parity is None or stopbits is None:
                    error_msg = f"Не все параметры подключения заданы корректно:\nCOM: {com_port}\nСкорость: {baud_rate}\nБиты: {bytesize}\nЧетность: {parity}\nСтоп-бит: {stopbits}"
                    QMessageBox.warning(self, "Ошибка", error_msg)
                    return
                
                # Открываем COM-порт с параметрами из выпадающих списков
                self.serial_port = serial.Serial(
                    port=com_port,
                    baudrate=baud_rate,
                    bytesize=bytesize,
                    parity=parity,
                    stopbits=stopbits,
                    timeout=None
                )
                
                # Проверяем, что параметры применились корректно
                if (self.serial_port.baudrate != baud_rate or 
                    self.serial_port.bytesize != bytesize or 
                    self.serial_port.parity != parity or 
                    self.serial_port.stopbits != stopbits):
                    warning_msg = (f"Параметры порта установлены некорректно.\n"
                                 f"Ожидалось: {baud_rate}, {bytesize}, {parity}, {stopbits}\n"
                                 f"Установлено: {self.serial_port.baudrate}, {self.serial_port.bytesize}, "
                                 f"{self.serial_port.parity}, {self.serial_port.stopbits}")
                    QMessageBox.warning(self, "Предупреждение", warning_msg)
                
                # Очищаем буфер порта после открытия
                self.serial_port.reset_input_buffer()
                self.serial_port.reset_output_buffer()
                
                self.is_connected = True
                
                # Запускаем поток для чтения из COM порта
                self.read_thread = threading.Thread(
                    target=read_from_com,
                    args=(self.serial_port, self.message_queue),
                    daemon=True
                )
                self.read_thread.start()
                
                # Запускаем поток для декодирования сообщений
                self.decode_thread = threading.Thread(
                    target=self.decode_messages,
                    daemon=True
                )
                self.decode_thread.start()
                self.connected_at = datetime.now()
                self.skip_first_invalid_crc = True
                # Сбрасываем флаг ожидания первого запроса при новом подключении
                self.waiting_for_first_request = True
                self.pushButton_connect.setText("Отключиться")
                QMessageBox.information(self, "Успех", f"Подключено к {com_port}")
                
            except Exception as e:
                QMessageBox.critical(self, "Ошибка подключения", str(e))
                if self.serial_port:
                    try:
                        self.serial_port.close()
                    except:
                        pass
                    self.serial_port = None
        else:
            # Отключение
            try:
                if self.serial_port:
                    self.serial_port.close()
                    self.serial_port = None
                # Ждем завершения потоков
                if self.decode_thread and self.decode_thread.is_alive():
                    # Поток завершится сам, так как serial_port закрыт
                    pass
                self.is_connected = False
                self.pushButton_connect.setText("Подключение")
                QMessageBox.information(self, "Информация", "Отключено от COM-порта")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка отключения", str(e))

    def decode_messages(self):
        """Декодирует сообщения из очереди в отдельном потоке"""
        while True:
            try:
                # Проверяем, подключены ли мы
                if not self.is_connected:
                    import time
                    time.sleep(0.1)
                    continue
                
                # Получаем сообщение из очереди с таймаутом
                message_hex = self.message_queue.get(timeout=0.1)
                try:
                    message_bytes = bytes.fromhex(message_hex)
                    if len(message_bytes) >= 4:  # Минимум адрес + функция + CRC (2 байта)
                        # Создаем объект Frame (декодирование в отдельном потоке)
                        frame = Frame(message_bytes)
                        
                        # Фильтр некорректных CRC в течение 1 секунды после подключения
                        if self.connected_at is not None:
                            time_since_connect = (datetime.now() - self.connected_at).total_seconds()
                            if time_since_connect < 1.0 and not frame.CRC_ok:
                                continue
                            # После 1 секунды отключаем этот фильтр
                            if time_since_connect >= 1.0:
                                self.connected_at = None
                        
                        # Старый одноразовый фильтр (на случай очень раннего пакета)
                        if self.skip_first_invalid_crc:
                            if not frame.CRC_ok:
                                self.skip_first_invalid_crc = False
                                continue
                            self.skip_first_invalid_crc = False
                        
                        # Кладим декодированное сообщение в очередь для обработки в GUI потоке
                        try:
                            self.decoded_queue.put_nowait((frame, message_bytes))  # Неблокирующая вставка
                        except queue.Full:
                            # Если очередь переполнена - пропускаем сообщение (GUI поток слишком медленный)
                            pass
                except Exception:
                    # Ошибка декодирования - пропускаем
                    pass
            except queue.Empty:
                continue
            except Exception:
                # Порт закрыт или другая ошибка - выходим из цикла
                break
    
    def process_decoded_messages(self):
        """Обрабатывает декодированные сообщения из очереди и добавляет в таблицу"""
        # Обрабатываем батчами для оптимизации (максимум 20 сообщений за раз)
        max_batch_size = 20
        processed_count = 0
        
        try:
            while processed_count < max_batch_size:
                try:
                    # Используем get_nowait для неблокирующего получения
                    frame, message_bytes = self.decoded_queue.get_nowait()
                    processed_count += 1
                except queue.Empty:
                    break
                
                # Проверяем, нужно ли ждать первого запроса
                # Оптимизация: определяем тип сообщения без полного get_list() где возможно
                base_function = frame.function & 0x7F
                
                # Быстрое определение типа для функций чтения (1-4)
                if base_function in (0x01, 0x02, 0x03, 0x04):
                    # Для функций чтения: если data[0] == длина данных-1 - это ответ
                    if len(frame.data) > 1 and frame.data[0] == len(frame.data) - 1:
                        message_type_value = "Ответ"
                    else:
                        message_type_value = "Запрос"
                else:
                    # Для остальных функций используем полный get_list (нужно для функций 5,6,15,16)
                    row_data = frame.get_list()
                    message_type_value = str(row_data[2]) if len(row_data) > 2 else "Запрос"
                
                if self.waiting_for_first_request:
                    # Если это ответ - отбрасываем его
                    if message_type_value == "Ответ":
                        continue
                    # Если это запрос с хорошей CRC - начинаем выводить
                    if message_type_value == "Запрос" and frame.CRC_ok:
                        self.waiting_for_first_request = False
                
                # Добавляем/обновляем строку
                self.add_or_update_row(frame, message_bytes)
                self.last_message_time = datetime.now()
        except queue.Empty:
            pass
        except Exception:
            # Ошибка обработки - пропускаем
            pass

    def add_or_update_row(self, frame: Frame, message_bytes: bytes):
        """Добавляет или обновляет строку под сообщение"""
        row_data = frame.get_list()
        message_type_value = str(row_data[2]) if len(row_data) > 2 else None
        now = datetime.now()

        # Ключи для поиска существующих строк
        base_function = frame.function & 0x7F  # для исключений (MSB=1) ищем по базовой функции
        is_exception = (frame.function & 0x80) != 0
        
        # Кэшируем hex для оптимизации (используется несколько раз)
        message_hex = message_bytes.hex()
        
        # Для функций 5 и 6 нужно проверить, является ли это ответом
        # Определяем по наличию соответствующего запроса или по порядку поступления
        # Важно: изменяем тип только для функций 5 и 6, для остальных используем тип из decode.py
        if base_function in (0x05, 0x06) and len(frame.data) == 4:
            # Создаем ключ для отслеживания одинаковых сообщений
            write_key = (frame.address, base_function, frame.data)
            
            # Проверяем, есть ли запрос с такими же данными (эхо запроса)
            if message_hex in self.request_index_by_bytes:
                # Это ответ на ранее полученный запрос
                message_type_value = "Ответ"
                row_data[2] = "Ответ"
            else:
                # Проверяем, сколько раз уже приходило такое же сообщение
                if write_key not in self.write_single_message_count:
                    self.write_single_message_count[write_key] = 0
                self.write_single_message_count[write_key] += 1
                
                # Если это второе одинаковое сообщение - первое было запросом, это ответ
                if self.write_single_message_count[write_key] == 2:
                    message_type_value = "Ответ"
                    row_data[2] = "Ответ"
                    # Для функции 6: когда приходит второе сообщение, нужно обновить счетчик запроса
                    # Запрос уже должен быть в request_index_by_bytes, так как сообщения обрабатываются последовательно
                    # Но если его там нет - значит это первое сообщение еще не было обработано
                    # В этом случае просто обрабатываем как ответ, запрос будет обработан позже
                # Для первого сообщения (счетчик == 1) оставляем тип "Запрос" из decode.py
        
        if message_type_value == "Запрос":
            req_key = message_bytes.hex()
            # Запоминаем время последнего запроса по адресу и функции
            self.last_request_time_by_af[(frame.address, base_function)] = now
            # Для функций 5 и 6 также запоминаем время по конкретному запросу
            if base_function in (0x05, 0x06) and len(frame.data) == 4:
                self.last_request_time_by_key[req_key] = now
            # Если такой запрос уже есть — обновляем время и счетчик
            if req_key in self.request_index_by_bytes:
                row_position = self.request_index_by_bytes[req_key]
                # Увеличиваем счетчик сообщений
                if req_key not in self.message_counters:
                    self.message_counters[req_key] = 0
                self.message_counters[req_key] += 1
                # Обновляем Время и счетчик
                time_item = QTableWidgetItem(now.strftime("%H:%M:%S.%f")[:-3])
                time_item.setBackground(QColor(215, 228, 242))  # запрос — светло-синий
                counter_item = QTableWidgetItem(str(self.message_counters[req_key]))
                counter_item.setBackground(QColor(215, 228, 242))
                self.SnifferTable.setItem(row_position, 1, time_item)
                self.SnifferTable.setItem(row_position, 0, counter_item)
                # Фильтры применяются только при изменении пользователем, не при каждом обновлении
                return
            pending_req_key = req_key
        elif message_type_value == "Ответ":
            # Подпись ответа: все поля кроме Счетчика, Времени и Данных (по колонкам)
            crc_hex = ' '.join(f'{b:02x}' for b in row_data[9]) if isinstance(row_data[9], bytes) else str(row_data[9])
            signature_tuple = (
                row_data[2],  # Тип сообщения
                row_data[3],  # Адрес
                row_data[4],  # Функция
                row_data[5],  # Адрес первого регистра / '-'
                row_data[6],  # Кол-во регистров/байт
                row_data[7],  # Количество байт далее (для функций 15, 16)
                crc_hex,      # CRC как строка
                row_data[10],  # CRC_OK
            )
            resp_key = str(signature_tuple)
            
            # Для функций 5 и 6: время ответа отсчитывается от последнего запроса с такими же данными
            # Для остальных функций: время от последнего запроса по (адрес, функция)
            delta_ms = None
            if base_function in (0x05, 0x06) and len(frame.data) == 4:
                # Для функций 5 и 6 ищем время последнего запроса с такими же данными
                if message_hex in self.last_request_time_by_key:
                    # Используем время конкретного запроса (последнего с такими же данными)
                    delta = now - self.last_request_time_by_key[message_hex]
                    delta_ms = int(delta.total_seconds() * 1000)
                elif (frame.address, base_function) in self.last_request_time_by_af:
                    # Если нет времени для конкретного запроса, используем общее время по адресу и функции
                    delta = now - self.last_request_time_by_af[(frame.address, base_function)]
                    delta_ms = int(delta.total_seconds() * 1000)
            else:
                # Для остальных функций используем стандартную логику
                if (frame.address, base_function) in self.last_request_time_by_af:
                    delta = now - self.last_request_time_by_af[(frame.address, base_function)]
                    delta_ms = int(delta.total_seconds() * 1000)
            
            time_display = f"+{delta_ms} ms" if delta_ms is not None else now.strftime("%H:%M:%S.%f")[:-3]

            # Для функций 5 и 6: при получении ответа увеличиваем счетчик соответствующего запроса
            if base_function in (0x05, 0x06) and len(frame.data) == 4:
                if message_hex in self.request_index_by_bytes:
                    # Запрос найден - увеличиваем его счетчик
                    req_row_pos = self.request_index_by_bytes[message_hex]
                    if message_hex not in self.message_counters:
                        self.message_counters[message_hex] = 0
                    self.message_counters[message_hex] += 1
                    # Обновляем счетчик запроса в таблице (оптимизация: проверяем видимость строки)
                    if not self.SnifferTable.isRowHidden(req_row_pos):
                        counter_item = QTableWidgetItem(str(self.message_counters[message_hex]))
                        counter_item.setBackground(QColor(215, 228, 242))
                        self.SnifferTable.setItem(req_row_pos, 0, counter_item)

            if resp_key in self.response_index_by_signature:
                row_position = self.response_index_by_signature[resp_key]
                # Увеличиваем счетчик сообщений
                if resp_key not in self.message_counters:
                    self.message_counters[resp_key] = 0
                self.message_counters[resp_key] += 1
                # Обновляем данные, время и счетчик
                data_value = row_data[8]  # Данные теперь в колонке 8
                if isinstance(data_value, bytes):
                    data_text = ' '.join(f'{byte:02x}' for byte in data_value) if data_value else '-'
                else:
                    data_text = str(data_value)
                data_item = QTableWidgetItem(data_text)
                # Определяем цвет фона: красный для исключений, зеленый для обычных ответов
                if is_exception:
                    bg_color = QColor(255, 199, 206)  # ответ-исключение — светло-красный
                else:
                    bg_color = QColor(226, 239, 218)  # ответ — светло-зеленый
                data_item.setBackground(bg_color)
                time_item = QTableWidgetItem(time_display)
                time_item.setBackground(bg_color)
                counter_item = QTableWidgetItem(str(self.message_counters[resp_key]))
                counter_item.setBackground(bg_color)
                self.SnifferTable.setItem(row_position, 8, data_item)  # Данные теперь в колонке 8
                self.SnifferTable.setItem(row_position, 1, time_item)
                self.SnifferTable.setItem(row_position, 0, counter_item)
                
                # Фильтры применяются только при изменении пользователем, не при каждом обновлении
                
                # Обновляем сохраненные данные сообщения
                try:
                    if isinstance(data_value, bytes):
                        self.message_data_storage[row_position] = (data_value, frame)
                    else:
                        # Парсим из строки
                        hex_bytes = data_text.replace(' ', '')
                        if len(hex_bytes) % 2 == 0:
                            data_bytes_parsed = bytes.fromhex(hex_bytes)
                            self.message_data_storage[row_position] = (data_bytes_parsed, frame)
                except Exception:
                    pass
                
                return
            pending_resp_key = resp_key

        # Сохраняем данные сообщения для окна "Значения" перед добавлением строки
        data_bytes = None
        if message_type_value == "Ответ":
            # Для ответов функций чтения данные начинаются после байта количества
            if base_function in (0x01, 0x02, 0x03, 0x04) and len(frame.data) > 1:
                data_bytes = frame.data[1:]  # пропускаем байт количества
            elif base_function in (0x05, 0x06):
                # Для функций 5 и 6 ответ содержит значение (2 байта после адреса)
                if len(frame.data) >= 4:
                    data_bytes = frame.data[2:4]  # значение (2 байта)
            else:
                data_bytes = frame.data
        else:
            # Для запросов функций чтения данные начинаются после первых 4 байт
            if base_function in (0x01, 0x02, 0x03, 0x04) and len(frame.data) > 4:
                data_bytes = frame.data[4:]
            elif base_function in (0x05, 0x06):
                # Для функций 5 и 6 запрос содержит значение (2 байта после адреса)
                if len(frame.data) >= 4:
                    data_bytes = frame.data[2:4]  # значение (2 байта)
            elif base_function in (0x0F, 0x10):
                # Для функций 15 и 16 данные начинаются после адреса (2) + количества (2) + байта количества (1)
                if len(frame.data) > 5:
                    data_bytes = frame.data[5:]  # значения после байта количества
            else:
                data_bytes = frame.data

        # Если не обновляли — добавляем новую строку
        if message_type_value == "Ответ":
            # Ищем последний запрос с таким же адресом и базовой функцией
            insert_after = None
            if (frame.address, base_function) in self.last_request_row_by_af:
                insert_after = self.last_request_row_by_af[(frame.address, base_function)]
            else:
                # Ищем в таблице последнюю строку-запрос с таким же адресом и базовой функцией
                insert_after = self.find_last_request_row(frame.address, base_function)
            
            if insert_after is not None:
                new_row_index = self.add_row_to_table(row_data, message_type_value, insert_row_index=insert_after + 1, data_bytes=data_bytes, frame=frame)
                # Сдвигаем индексы из словарей после вставки
                try:
                    for k in list(self.request_index_by_bytes.keys()):
                        if self.request_index_by_bytes[k] >= insert_after + 1:
                            self.request_index_by_bytes[k] += 1
                    for k in list(self.response_index_by_signature.keys()):
                        if self.response_index_by_signature[k] >= insert_after + 1:
                            self.response_index_by_signature[k] += 1
                    for k in list(self.last_request_row_by_af.keys()):
                        if self.last_request_row_by_af[k] >= insert_after + 1:
                            self.last_request_row_by_af[k] += 1
                except Exception:
                    pass
            else:
                # Соответствующий запрос не найден
                # Если таблица пустая, сохраняем ответ, иначе добавляем в конец
                if self.SnifferTable.rowCount() == 0:
                    # Таблица пустая - сохраняем ответ во временном хранилище
                    key = (frame.address, base_function)
                    if key not in self.pending_responses:
                        self.pending_responses[key] = []
                    self.pending_responses[key].append((row_data, frame, message_bytes))
                    return  # Не добавляем ответ в таблицу, пока не появится запрос
                else:
                    # В таблице уже есть строки - добавляем ответ в конец (возможно, запрос будет добавлен позже)
                    new_row_index = self.add_row_to_table(row_data, message_type_value, data_bytes=data_bytes, frame=frame)
                    try:
                        self.response_index_by_signature[pending_resp_key] = new_row_index
                    except Exception:
                        pass
                    # Сохраняем данные сообщения
                    if data_bytes is not None:
                        self.message_data_storage[new_row_index] = (data_bytes, frame)
                    return
        else:
            # Для запросов добавляем в конец
            new_row_index = self.add_row_to_table(row_data, message_type_value, data_bytes=data_bytes, frame=frame)
        
        # Сохраняем данные сообщения
        if data_bytes is not None:
            self.message_data_storage[new_row_index] = (data_bytes, frame)
        
        # Зафиксируем индексы для последующих обновлений
        try:
            if message_type_value == "Запрос":
                self.request_index_by_bytes[pending_req_key] = new_row_index
                self.last_request_row_by_af[(frame.address, base_function)] = new_row_index
                # Инициализируем счетчик для нового запроса
                if pending_req_key not in self.message_counters:
                    self.message_counters[pending_req_key] = 1
                # Проверяем, есть ли ожидающие ответы для этого запроса
                key = (frame.address, base_function)
                if key in self.pending_responses and self.pending_responses[key]:
                    # Вставляем все ожидающие ответы сразу после запроса
                    pending_list = self.pending_responses.pop(key)
                    current_insert_after = new_row_index
                    for pending_row_data, pending_frame, pending_msg_bytes in pending_list:
                        self.add_or_update_row(pending_frame, pending_msg_bytes)
            else:
                self.response_index_by_signature[pending_resp_key] = new_row_index
                # Инициализируем счетчик для нового ответа
                if pending_resp_key not in self.message_counters:
                    self.message_counters[pending_resp_key] = 1
        except Exception:
            pass

    def find_last_request_row(self, address, base_function):
        """Ищет в таблице последнюю строку-запрос с указанным адресом и базовой функцией"""
        for row in range(self.SnifferTable.rowCount() - 1, -1, -1):
            try:
                # Проверяем тип сообщения (колонка 2)
                msg_type_item = self.SnifferTable.item(row, 2)
                if msg_type_item and msg_type_item.text() == "Запрос":
                    # Проверяем адрес (колонка 3)
                    addr_item = self.SnifferTable.item(row, 3)
                    # Проверяем функцию (колонка 4)
                    func_item = self.SnifferTable.item(row, 4)
                    if addr_item and func_item:
                        try:
                            row_addr = int(addr_item.text())
                            row_func = int(func_item.text())
                            row_base_func = row_func & 0x7F
                            if row_addr == address and row_base_func == base_function:
                                return row
                        except (ValueError, AttributeError):
                            continue
            except Exception:
                continue
        return None

    def add_row_to_table(self, row_data, message_type_value=None, insert_row_index=None, data_bytes=None, frame=None):
        """Функция добавления новой строки к таблице"""
        # Страхуемся на случай расхождения числа колонок и данных
        try:
            if self.SnifferTable.columnCount() < len(row_data):
                self.SnifferTable.setColumnCount(len(row_data))
        except Exception:
            pass
        # Получаем позицию вставки в таблице
        if insert_row_index is None:
            row_position = self.SnifferTable.rowCount()
            self.SnifferTable.insertRow(row_position)
        else:
            row_position = insert_row_index
            self.SnifferTable.insertRow(row_position)
        
        # Получаем текущее время
        current_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        # Для новой строки счетчик всегда 1 (счетчик инициализируется при добавлении индекса)
        message_counter_value = 1
        # Определяем тип для окраски строки
        if message_type_value is None:
            try:
                message_type_value = str(row_data[2]) if len(row_data) > 2 else None
            except Exception:
                message_type_value = None
        if message_type_value == "Запрос":
            row_color = QColor(215, 228, 242)  # light blue background
        elif message_type_value == "Ответ":
            # Если это ответ-исключение (MSB функции = 1), красим в светло-красный
            try:
                func_val = int(row_data[4])
            except Exception:
                func_val = 0
            if (func_val & 0x80) != 0:
                row_color = QColor(255, 199, 206)  # light red background
            else:
                row_color = QColor(226, 239, 218)  # light green background
        else:
            row_color = None
        
        # Заполняем ячейки новыми данными
        for column, data in enumerate(row_data):
            # Нулевая колонка - счетчик уникальных сообщений
            if column == 0:
                data = message_counter_value
            # Первая колонка - время
            elif column == 1:
                data = current_time
            
            # Если data является байтовыми данными, преобразуем их в строку
            if isinstance(data, bytes):  # Преобразуем байты в строку в шестнадцатеричном формате
                data = ' '.join(f'{byte:02x}' for byte in data) 
            else:  # остальные значения просто преобразовываем в строку
                data = str(data)
            item = QTableWidgetItem(data)  # Создаем новый элемент для ячейки
            if row_color is not None:
                item.setBackground(row_color)
            # Включаем перенос текста для колонки "Количество регистров/байт" (колонка 6) для сообщений об ошибках
            if column == 6:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                # Убеждаемся, что перенос текста включен для этой ячейки
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)
            self.SnifferTable.setItem(row_position, column, item)  # Устанавливаем элемент в таблицу
            # После добавления всех ячеек в строке обновляем высоту строки, если в колонке 6 длинный текст
            if column == len(row_data) - 1:  # Последняя колонка в строке
                col6_item = self.SnifferTable.item(row_position, 6)
                if col6_item:
                    col6_text = col6_item.text()
                    # Проверяем, содержит ли текст "Ошибка:" или длиннее 15 символов
                    if "Ошибка:" in col6_text or len(col6_text) > 15:
                        # Принудительно обновляем высоту строки для переноса текста
                        self.SnifferTable.resizeRowToContents(row_position)

        # Обновляем списки фильтров (адрес и функция)
        # Колонка 3 - адрес, колонка 4 - функция
        if len(row_data) > 3:
            try:
                address_str = str(row_data[3])
                # Проверяем, есть ли уже такой адрес в списке
                if address_str and self.comboBox_filter_address.findText(address_str) == -1:
                    self.comboBox_filter_address.addItem(address_str)
            except Exception:
                pass
        
        if len(row_data) > 4:
            try:
                func_val = int(row_data[4])
                # Для ошибок берем базовую функцию (без MSB)
                base_func = func_val & 0x7F
                func_str = str(base_func)
                # Проверяем, есть ли уже такая функция в списке
                if func_str and self.comboBox_filter_function.findText(func_str) == -1:
                    self.comboBox_filter_function.addItem(func_str)
            except Exception:
                pass

        # Сохраняем данные сообщения для окна "Значения"
        if data_bytes is not None and frame is not None:
            self.message_data_storage[row_position] = (data_bytes, frame)

        # Применяем фильтры к новой строке
        self.apply_filters()

        return row_position

    def process_pending_responses(self):
        """Обрабатывает ожидающие ответы: проверяет наличие запросов и вставляет оставшиеся в конец"""
        if not self.pending_responses:
            return
        
        # Если прошло больше 1 секунды без новых сообщений, считаем, что все сообщения получены
        if self.last_message_time is None:
            return
        
        time_since_last = (datetime.now() - self.last_message_time).total_seconds()
        if time_since_last < 1.0:
            return  # Слишком рано, еще могут прийти сообщения
        
        # Проверяем каждую группу ожидающих ответов
        keys_to_process = list(self.pending_responses.keys())
        for key in keys_to_process:
            address, base_function = key
            pending_list = self.pending_responses.get(key, [])
            if not pending_list:
                continue
            
            # Проверяем, появился ли запрос для этих ответов
            if key in self.last_request_row_by_af:
                # Запрос найден - вставляем ответы после него
                insert_after = self.last_request_row_by_af[key]
                for pending_row_data, pending_frame, pending_msg_bytes in pending_list:
                    try:
                        self.add_or_update_row(pending_frame, pending_msg_bytes)
                    except Exception:
                        pass
                # Удаляем обработанную группу
                del self.pending_responses[key]
            else:
                # Запроса все еще нет - если прошло достаточно времени, вставляем ответы в конец
                if time_since_last >= 2.0:  # 2 секунды без новых сообщений
                    # Вставляем все ожидающие ответы в конец таблицы
                    for pending_row_data, pending_frame, pending_msg_bytes in pending_list:
                        try:
                            pending_row_data_local = pending_frame.get_list()
                            pending_message_type = str(pending_row_data_local[2]) if len(pending_row_data_local) > 2 else None
                            new_row_index = self.add_row_to_table(pending_row_data_local, pending_message_type)
                            # Сохраняем индекс для последующих обновлений
                            try:
                                pending_crc_hex = ' '.join(f'{b:02x}' for b in pending_row_data_local[9]) if isinstance(pending_row_data_local[9], bytes) else str(pending_row_data_local[9])
                                pending_signature = (
                                    pending_row_data_local[2],  # Тип сообщения
                                    pending_row_data_local[3],  # Адрес
                                    pending_row_data_local[4],  # Функция
                                    pending_row_data_local[5],  # Адрес первого регистра / '-'
                                    pending_row_data_local[6],  # Кол-во регистров/байт
                                    pending_row_data_local[7],  # Количество байт далее (для функций 15, 16)
                                    pending_crc_hex,
                                    pending_row_data_local[10],  # CRC_OK
                                )
                                pending_resp_key = str(pending_signature)
                                self.response_index_by_signature[pending_resp_key] = new_row_index
                            except Exception:
                                pass
                        except Exception:
                            pass
                    # Удаляем обработанную группу
                    del self.pending_responses[key]

    def apply_filters(self):
        """Применяет фильтры к таблице"""
        filter_crc_ok = self.checkBox_filter_crc_ok.isChecked()
        filter_errors_only = self.checkBox_filter_errors_only.isChecked()
        
        # Получаем значения фильтров адреса и функции
        filter_address_text = self.comboBox_filter_address.currentText().strip()
        filter_function_text = self.comboBox_filter_function.currentText().strip()
        
        # Парсим значения фильтров (если пусто - фильтр не применяется)
        filter_address = None
        if filter_address_text:
            try:
                filter_address = int(filter_address_text)
            except ValueError:
                filter_address = None
        
        filter_function = None
        if filter_function_text:
            try:
                filter_function = int(filter_function_text)
            except ValueError:
                filter_function = None
        
        for row in range(self.SnifferTable.rowCount()):
            # Проверяем CRC_OK (колонка 10)
            crc_ok_item = self.SnifferTable.item(row, 10)
            crc_ok_value = False
            if crc_ok_item:
                try:
                    crc_ok_text = str(crc_ok_item.text()).lower()
                    crc_ok_value = crc_ok_text in ('true', '1', 'да')
                except Exception:
                    crc_ok_value = False
            
            # Проверяем, является ли сообщение ошибкой (колонка 4 - Функция)
            is_error = False
            func_item = self.SnifferTable.item(row, 4)
            func_val = None
            if func_item:
                try:
                    func_val = int(func_item.text())
                    is_error = (func_val & 0x80) != 0
                except Exception:
                    is_error = False
            
            # Проверяем адрес (колонка 3)
            address_val = None
            address_item = self.SnifferTable.item(row, 3)
            if address_item:
                try:
                    address_val = int(address_item.text())
                except Exception:
                    address_val = None
            
            # Определяем, должна ли строка быть видимой
            should_show = True
            
            # Фильтр по CRC_OK: скрывать невалидные CRC
            if filter_crc_ok and not crc_ok_value:
                should_show = False
            
            # Фильтр по ошибкам: показывать только ошибки
            if filter_errors_only and not is_error:
                should_show = False
            
            # Фильтр по адресу
            if filter_address is not None and address_val is not None:
                if address_val != filter_address:
                    should_show = False
            
            # Фильтр по функции (с учетом ошибок - для ошибок проверяем базовую функцию)
            if filter_function is not None and func_val is not None:
                # Для ошибок берем базовую функцию (без MSB)
                base_func_val = func_val & 0x7F
                if base_func_val != filter_function:
                    should_show = False
            
            # Применяем видимость
            self.SnifferTable.setRowHidden(row, not should_show)

    def reset_all_filters(self):
        """Сбрасывает все фильтры к значениям по умолчанию"""
        # Сбрасываем чекбоксы
        self.checkBox_filter_crc_ok.setChecked(False)
        self.checkBox_filter_errors_only.setChecked(False)
        
        # Очищаем выпадающие списки (оставляем только пустой элемент)
        self.comboBox_filter_address.setCurrentText("")
        self.comboBox_filter_function.setCurrentText("")
        
        # Применяем фильтры (чтобы показать все строки)
        self.apply_filters()
    
    def clear_table(self):
        """Очищает таблицу Сниффер полностью и сбрасывает все счетчики и индексы"""
        # Очищаем таблицу
        self.SnifferTable.setRowCount(0)
        
        # Очищаем списки фильтров (оставляем только пустой элемент)
        self.comboBox_filter_address.clear()
        self.comboBox_filter_address.addItem("")
        self.comboBox_filter_function.clear()
        self.comboBox_filter_function.addItem("")
        
        # Очищаем все словари и индексы
        self.request_index_by_bytes.clear()
        self.response_index_by_signature.clear()
        self.last_request_time_by_af.clear()
        self.last_request_row_by_af.clear()
        self.pending_responses.clear()
        self.message_counters.clear()
        
        # Сбрасываем счетчики
        self.message_counter = 0
        self.last_message_time = None
        
        # Применяем фильтры (на случай, если они включены)
        self.apply_filters()
        
        # Очищаем хранилище данных
        self.message_data_storage.clear()
        self.register_types_storage.clear()
        
        # Сбрасываем флаг ожидания первого запроса после очистки
        self.waiting_for_first_request = True

    def on_row_selected(self):
        """Обработчик выбора строки в таблице - заполняет окно "Значения" """
        selected_rows = self.SnifferTable.selectedIndexes()
        if not selected_rows:
            # Очищаем окно значений, если ничего не выбрано
            self.ValuesTable.setRowCount(0)
            return
        
        # Берем первую выбранную строку
        row_position = selected_rows[0].row()
        
        # Проверяем наличие данных в столбце "Данные" (колонка 8)
        data_item = self.SnifferTable.item(row_position, 8)  # Данные теперь в колонке 8
        if not data_item:
            # Если ячейка не существует, показываем прочерк
            self.ValuesTable.setRowCount(1)
            self.ValuesTable.setItem(0, 0, QTableWidgetItem("-"))
            self.ValuesTable.setItem(0, 1, QTableWidgetItem("-"))
            self.ValuesTable.setItem(0, 2, QTableWidgetItem("-"))
            # Убираем выпадающий список из колонки типов данных
            self.ValuesTable.setCellWidget(0, 1, None)
            return
        
        data_text = data_item.text().strip()
        
        # Проверяем, есть ли данные (не пустая строка и не прочерк)
        if not data_text or data_text == "-" or data_text == "":
            # Нет данных - показываем прочерк
            self.ValuesTable.setRowCount(1)
            self.ValuesTable.setItem(0, 0, QTableWidgetItem("-"))
            self.ValuesTable.setItem(0, 1, QTableWidgetItem("-"))
            self.ValuesTable.setItem(0, 2, QTableWidgetItem("-"))
            # Убираем выпадающий список из колонки типов данных
            self.ValuesTable.setCellWidget(0, 1, None)
            return
        
        # Получаем данные сообщения
        data_bytes = None
        frame = None
        
        if row_position in self.message_data_storage:
            data_bytes, frame = self.message_data_storage[row_position]
        else:
            # Пытаемся получить данные из ячейки таблицы "Данные" (колонка 8)
            try:
                # Парсим hex строку вида "aa bb cc dd"
                hex_bytes = data_text.replace(' ', '')
                if len(hex_bytes) % 2 == 0 and len(hex_bytes) > 0:
                    data_bytes = bytes.fromhex(hex_bytes)
                else:
                    # Невалидные данные - показываем прочерк
                    self.ValuesTable.setRowCount(1)
                    self.ValuesTable.setItem(0, 0, QTableWidgetItem("-"))
                    self.ValuesTable.setItem(0, 1, QTableWidgetItem("-"))
                    self.ValuesTable.setItem(0, 2, QTableWidgetItem("-"))
                    # Убираем выпадающий список из колонки типов данных
                    self.ValuesTable.setCellWidget(0, 1, None)
                    return
            except Exception:
                # Ошибка парсинга - показываем прочерк
                self.ValuesTable.setRowCount(1)
                self.ValuesTable.setItem(0, 0, QTableWidgetItem("-"))
                self.ValuesTable.setItem(0, 1, QTableWidgetItem("-"))
                self.ValuesTable.setItem(0, 2, QTableWidgetItem("-"))
                # Убираем выпадающий список из колонки типов данных
                self.ValuesTable.setCellWidget(0, 1, None)
                return
        
        if data_bytes is None or len(data_bytes) == 0:
            # Нет данных - показываем прочерк
            self.ValuesTable.setRowCount(1)
            self.ValuesTable.setItem(0, 0, QTableWidgetItem("-"))
            self.ValuesTable.setItem(0, 1, QTableWidgetItem("-"))
            self.ValuesTable.setItem(0, 2, QTableWidgetItem("-"))
            # Убираем выпадающий список из колонки типов данных
            self.ValuesTable.setCellWidget(0, 1, None)
            return
        
        # Вычисляем количество регистров (по умолчанию количество байт / 2)
        num_registers = len(data_bytes) // 2
        
        # Загружаем сохраненные типы для этого сообщения
        if row_position not in self.register_types_storage:
            self.register_types_storage[row_position] = ["Signed"] * num_registers
        
        register_types = self.register_types_storage[row_position]
        
        # Убеждаемся, что количество типов соответствует количеству регистров
        while len(register_types) < num_registers:
            register_types.append("Signed")
        
        # Заполняем таблицу регистров
        self.ValuesTable.setRowCount(num_registers)
        reserved_registers = set()  # Регистры, зарезервированные 4-байтными типами
        
        # Определяем зарезервированные регистры на основе сохраненных типов
        for reg_idx in range(num_registers):
            if reg_idx < len(register_types):
                reg_type = register_types[reg_idx]
                if reg_type in ["float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"]:
                    # Этот регистр занимает 4 байта (2 регистра), следующий зарезервирован
                    if reg_idx + 1 < num_registers:
                        reserved_registers.add(reg_idx + 1)
        
        for reg_idx in range(num_registers):
            # Колонка 0: номер регистра
            reg_item = QTableWidgetItem(f"Регистр {reg_idx}")
            if reg_idx in reserved_registers:
                # Устанавливаем серый цвет для зарезервированных регистров
                gray_color = QColor(220, 220, 220)  # Светло-серый цвет
                reg_item.setBackground(gray_color)
            reg_item.setFlags(reg_item.flags() | Qt.ItemFlag.ItemIsEnabled)
            self.ValuesTable.setItem(reg_idx, 0, reg_item)
            
            # Колонка 1: выпадающий список типов данных
            type_combo = QComboBox()
            type_combo.addItems(["Signed", "Unsigned", "HEX", "Binary", "float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"])
            
            # Устанавливаем сохраненный тип
            if reg_idx < len(register_types):
                current_type = register_types[reg_idx]
                index = type_combo.findText(current_type)
                if index >= 0:
                    type_combo.setCurrentIndex(index)
            
            # Если регистр зарезервирован - отключаем выбор типа
            if reg_idx in reserved_registers:
                type_combo.setEnabled(False)
                reg_item.setFlags(reg_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                # Устанавливаем серый цвет для комбобокса (через стиль)
                type_combo.setStyleSheet("background-color: rgb(220, 220, 220);")
            else:
                # Подключаем обработчик изменения типа
                type_combo.currentTextChanged.connect(lambda text, r=reg_idx, rp=row_position: self.on_register_type_changed(rp, r, text))
            
            self.ValuesTable.setCellWidget(reg_idx, 1, type_combo)
            
            # Колонка 2: значение (будет заполнено после выбора типа)
            value_item = QTableWidgetItem("")
            if reg_idx in reserved_registers:
                # Устанавливаем серый цвет для зарезервированных регистров
                gray_color = QColor(220, 220, 220)
                value_item.setBackground(gray_color)
            self.ValuesTable.setItem(reg_idx, 2, value_item)
        
        # Обновляем значения для всех регистров
        self.update_register_values(row_position, data_bytes)

    def on_register_type_changed(self, row_position, reg_idx, new_type):
        """Обработчик изменения типа данных регистра"""
        # Получаем старый тип
        old_type = None
        if row_position in self.register_types_storage and reg_idx < len(self.register_types_storage[row_position]):
            old_type = self.register_types_storage[row_position][reg_idx]
        
        # Сохраняем новый тип
        if row_position not in self.register_types_storage:
            self.register_types_storage[row_position] = []
        
        register_types = self.register_types_storage[row_position]
        while len(register_types) <= reg_idx:
            register_types.append("Signed")
        
        register_types[reg_idx] = new_type
        
        # Если старый тип был 4-байтным, а новый нет - разблокируем следующий регистр
        if old_type and old_type in ["float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"]:
            if new_type not in ["float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"]:
                if reg_idx + 1 < self.ValuesTable.rowCount():
                    next_combo = self.ValuesTable.cellWidget(reg_idx + 1, 1)
                    if next_combo:
                        next_combo.setEnabled(True)
                next_item = self.ValuesTable.item(reg_idx + 1, 0)
                if next_item:
                    next_item.setText(f"Регистр {reg_idx + 1}")
                    next_item.setFlags(next_item.flags() | Qt.ItemFlag.ItemIsEnabled)
                    # Убираем серый цвет
                    next_item.setBackground(QColor(255, 255, 255))
                # Также обновляем цвет комбобокса и значения
                next_combo = self.ValuesTable.cellWidget(reg_idx + 1, 1)
                if next_combo:
                    next_combo.setStyleSheet("")
                next_value_item = self.ValuesTable.item(reg_idx + 1, 2)
                if next_value_item:
                    next_value_item.setBackground(QColor(255, 255, 255))
        
        # Если выбран 4-байтный тип, резервируем следующий регистр
        if new_type in ["float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"]:
            if reg_idx + 1 < self.ValuesTable.rowCount():
                next_combo = self.ValuesTable.cellWidget(reg_idx + 1, 1)
                if next_combo:
                    next_combo.setEnabled(False)
                    next_combo.setCurrentIndex(0)  # Сбрасываем на первый тип
                next_item = self.ValuesTable.item(reg_idx + 1, 0)
                if next_item:
                    next_item.setText(f"Регистр {reg_idx + 1}")
                    next_item.setFlags(next_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    # Устанавливаем серый цвет для зарезервированного регистра
                    gray_color = QColor(220, 220, 220)
                    next_item.setBackground(gray_color)
                # Также устанавливаем серый цвет для комбобокса и значения
                next_combo.setStyleSheet("background-color: rgb(220, 220, 220);")
                next_value_item = self.ValuesTable.item(reg_idx + 1, 2)
                if next_value_item:
                    next_value_item.setBackground(gray_color)
        
        # Обновляем значения
        if row_position in self.message_data_storage:
            data_bytes, _ = self.message_data_storage[row_position]
            self.update_register_values(row_position, data_bytes)

    def update_register_values(self, row_position, data_bytes):
        """Обновляет значения регистров в окне "Значения" """
        if row_position not in self.register_types_storage:
            return
        
        register_types = self.register_types_storage[row_position]
        num_registers = len(data_bytes) // 2
        
        for reg_idx in range(min(num_registers, self.ValuesTable.rowCount())):
            # Проверяем, не зарезервирован ли этот регистр предыдущим 4-байтным типом
            if reg_idx > 0 and reg_idx - 1 < len(register_types):
                prev_type = register_types[reg_idx - 1]
                if prev_type in ["float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"]:
                    # Этот регистр зарезервирован - оставляем пустым
                    value_item = self.ValuesTable.item(reg_idx, 2)
                    if value_item:
                        value_item.setText("")
                        # Убеждаемся, что серый цвет сохранен
                        gray_color = QColor(220, 220, 220)
                        value_item.setBackground(gray_color)
                    continue
            
            if reg_idx >= len(register_types):
                continue
            
            reg_type = register_types[reg_idx]
            
            # Получаем байты для регистра
            if reg_type in ["float (ABCD)", "float (CDAB)", "float (BADC)", "float (DCBA)", "long (ABCD)", "long (CDAB)", "long (BADC)", "long (DCBA)"]:
                # 4-байтный тип - берем 2 регистра (4 байта)
                start_byte = reg_idx * 2
                if start_byte + 3 < len(data_bytes):
                    bytes_for_value = data_bytes[start_byte:start_byte + 4]
                else:
                    value_item = self.ValuesTable.item(reg_idx, 2)
                    if value_item:
                        value_item.setText("Недостаточно данных")
                    continue
            else:
                # 2-байтный тип
                start_byte = reg_idx * 2
                if start_byte + 1 < len(data_bytes):
                    bytes_for_value = data_bytes[start_byte:start_byte + 2]
                else:
                    value_item = self.ValuesTable.item(reg_idx, 2)
                    if value_item:
                        value_item.setText("Недостаточно данных")
                    continue
            
            # Преобразуем значение в зависимости от типа
            value_str = self.convert_register_value(bytes_for_value, reg_type, reg_idx, data_bytes)
            
            # Обновляем значение в таблице
            value_item = self.ValuesTable.item(reg_idx, 2)
            if value_item:
                value_item.setText(value_str)

    def convert_register_value(self, bytes_data, reg_type, reg_idx, all_data_bytes):
        """Преобразует байты регистра в значение согласно типу"""
        try:
            if reg_type == "Signed":
                if len(bytes_data) == 2:
                    value = struct.unpack('>h', bytes_data)[0]  # big-endian signed short
                    return str(value)
            
            elif reg_type == "Unsigned":
                if len(bytes_data) == 2:
                    value = struct.unpack('>H', bytes_data)[0]  # big-endian unsigned short
                    return str(value)
            
            elif reg_type == "HEX":
                if len(bytes_data) == 2:
                    return f"0x{bytes_data[0]:02X}{bytes_data[1]:02X}"
            
            elif reg_type == "Binary":
                if len(bytes_data) == 2:
                    return f"{bytes_data[0]:08b} {bytes_data[1]:08b}"
            
            elif reg_type.startswith("float"):
                if len(bytes_data) == 4:
                    # Определяем порядок байт
                    order = reg_type.split("(")[1].split(")")[0]
                    reordered = self.reorder_bytes(bytes_data, order, reg_idx, all_data_bytes)
                    # После переупорядочивания байты в формате big-endian
                    value = struct.unpack('>f', reordered)[0]  # big-endian float
                    return f"{value:.6f}"
            
            elif reg_type.startswith("long"):
                if len(bytes_data) == 4:
                    # Определяем порядок байт
                    order = reg_type.split("(")[1].split(")")[0]
                    reordered = self.reorder_bytes(bytes_data, order, reg_idx, all_data_bytes)
                    # После переупорядочивания байты в формате big-endian
                    value = struct.unpack('>l', reordered)[0]  # big-endian signed long
                    return str(value)
        
        except Exception as e:
            return f"Ошибка: {e}"
        
        return "Неизвестный тип"

    def reorder_bytes(self, bytes_data, order, reg_idx, all_data_bytes):
        """Переупорядочивает байты согласно порядку ABCD, CDAB, BADC, DCBA"""
        # bytes_data содержит 4 байта для float/long
        # order: ABCD, CDAB, BADC, DCBA
        # ABCD - байты [0,1,2,3] (обычный порядок)
        # CDAB - байты [2,3,0,1] (поменять местами регистры)
        # BADC - байты [1,0,3,2] (поменять байты в каждом регистре)
        # DCBA - байты [3,2,1,0] (полная инверсия)
        
        if order == "ABCD":
            return bytes_data
        elif order == "CDAB":
            return bytes([bytes_data[2], bytes_data[3], bytes_data[0], bytes_data[1]])
        elif order == "BADC":
            return bytes([bytes_data[1], bytes_data[0], bytes_data[3], bytes_data[2]])
        elif order == "DCBA":
            return bytes([bytes_data[3], bytes_data[2], bytes_data[1], bytes_data[0]])
        else:
            return bytes_data

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()  # Показываем окно
    sys.exit(app.exec())  # Запуск главного цикла приложения
 


