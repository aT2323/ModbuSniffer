import serial
import serial.tools.list_ports
import time
import queue

def read_list_ports():
    """
    Читает и возвращаетсписок доступных COM-портов
    """
    ports = serial.tools.list_ports.comports()
    list_ports = [port.device for port in ports]
    if not list_ports :
        raise ValueError("No COM port found.")
    return list_ports
    
def open_serial_port(port, baudrate, bytesize, parity):
    """
    Открывает и возвращает объект Serial.
    
    :param port: Номер COM порта
    :param baudrate: Скорость передачи данных
    :return: Объект Serial
    """
    try:
        ser = serial.Serial(port, baudrate, bytesize, parity, timeout = None)
    except ValueError as ve:
        print("Error:", str(ve))
    return ser
    
def read_from_com(ser: serial.Serial, message_queue, enClear=False):
    """
    Читает данные из COM-порта и определяет границы Modbus RTU сообщений.
    Сообщения определяются по паузе 3.5 символа между байтами.
    
    :param ser: Объект Serial для чтения
    :param message_queue: Очередь для передачи сообщений
    :param enClear: Режим очистки буфера при частичных сообщениях
    """
    buffer = bytearray()  # Создаем пустой bytearray для хранения данных
    last_time = time.time()  # Время получения последнего байта
    k_transmission = 1
    symbol_time = k_transmission * 11 / ser.baudrate  # Пауза между символами в секундах
    timeout_check = 3.5 * symbol_time  # Таймаут для определения конца сообщения
    
    try:
        while ser.is_open:
            # Проверяем наличие данных
            if ser.in_waiting > 0:
                current_time = time.time()
                time_diff = current_time - last_time
                # Читаем новый байт из COM порта
                byte = ser.read()
                last_time = current_time

                # Если пауза больше 3.5 символов (полное сообщение), выводим его
                if time_diff >= timeout_check:
                    if buffer:
                        message_hex = buffer.hex()
                        try:
                            message_queue.put_nowait(message_hex)  # Неблокирующая вставка
                        except queue.Full:
                            # Если очередь переполнена - пропускаем старое сообщение
                            pass
                        buffer.clear()  # Очищаем буфер

                # Если активирован разборчивый режим и пауза больше 1.5 символа, но меньше 3.5 символов
                elif enClear and time_diff > 1.5 * symbol_time:
                    buffer.clear()  # Очищаем буфер

                buffer.extend(byte)  # Добавляем байт в буфер
            else:
                # Если нет данных, проверяем, не нужно ли отправить сообщение из буфера
                # (если прошло достаточно времени после последнего байта)
                if buffer:
                    current_time = time.time()
                    time_diff = current_time - last_time
                    if time_diff >= timeout_check:
                        message_hex = buffer.hex()
                        try:
                            message_queue.put_nowait(message_hex)  # Неблокирующая вставка
                        except queue.Full:
                            # Если очередь переполнена - пропускаем старое сообщение
                            pass
                        buffer.clear()
                time.sleep(0.01)  # Небольшая задержка, чтобы не нагружать CPU
                
    except (serial.SerialException, OSError):
        # Порт закрыт или произошла ошибка
        pass
    finally:
        # Отправляем последнее сообщение из буфера, если оно есть
        if buffer:
            message_hex = buffer.hex()
            try:
                message_queue.put_nowait(message_hex)  # Неблокирующая вставка
            except queue.Full:
                pass

if __name__ == '__main__':
    try:
        list_ports = read_list_ports()
        ser = open_serial_port('COM15',19200,8,'E')
        message_queue = queue.Queue()
        while True:
            read_from_com(ser, message_queue)
    except ValueError as ve:
        print("Error:", str(ve))

    except serial.SerialException as se:
        print("Serial port error:", str(se))

    except Exception as e:
        print("An error occurred:", str(e))
