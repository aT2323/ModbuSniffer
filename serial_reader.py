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
    
def read_from_com(ser: serial, message_queue, enClear=False):

    buffer = bytearray()  # Создаем пустой bytearray для хранения данных
    last_time = time.time()  # Время получения последнего байта
    k_transmission = 1
    symbol_time = k_transmission * 11 / ser.baudrate  # Пауза между символами в секундах
    #print(symbol_time)
    while True:
        if ser.in_waiting > 0:
            current_time = time.time()
            time_diff = current_time - last_time
            # Читаем новый байт из COM порта
            # if ser.in_waiting > 1:
            #     print("More 1 byte")
            byte = ser.read()
            last_time = current_time

            # Если пауза больше 3.5 символов (полное сообщение), выводим его
            if (time_diff >= 3.5 * symbol_time):  # при разрыве сообщение обычно пауза доходит до +- 8 символов. А настоящая пауза длится от 30 символов, но общение 1 в 1
                if buffer:
                    message_hex = buffer.hex()
                    message_queue.put(message_hex)
                    print(time_diff)                  # для отладки
                    print(message_hex)                # для отладки
                    buffer.clear()  # Очищаем буфер

            # Если активирован разборчивый режим и пауза больше 1.5 символа, но меньше 3.5 символов
            elif enClear and time_diff > 1.5 * symbol_time:
                #print(time_diff/symbol_time)
                buffer.clear()  # Очищаем буфер

            buffer.extend(byte)  # Добавляем байт в буфер

if __name__ == '__main__':
    try:
        list_ports = read_list_ports()
        ser = open_serial_port('COM23',19200,8,'N')
        message_queue = queue.Queue()
        while True:
            read_from_com(ser, message_queue)
    except ValueError as ve:
        print("Error:", str(ve))

    except serial.SerialException as se:
        print("Serial port error:", str(se))

    except Exception as e:
        print("An error occurred:", str(e))
