class Frame:
    def __init__(self, message: bytes):
        if len(message) < 4:
            raise ValueError(f"Сообщение слишком короткое для Modbus RTU: {len(message)} байт (минимум 4)")
        
        self.message = message
        # Параметры сообщения Modbus RTU:
        # Первые 2 байта - адрес и функция
        self.address = message[0]
        self.function = message[1]
        # Все байты, кроме первых 2 и последних 2 (CRC)
        self.data = message[2:-2]
        # Последние 2 байта - это CRC
        self.received_crc = message[-2:]
        
        # Атрибут для хранения состояния корректности CRC
        self.CRC_ok = False
        # Вычисляем CRC и проверяем его при создании объекта
        self.check_crc()

    def __repr__(self):
        # Представление кадра в виде строки
        return f"Frame(address={self.address}, function={self.function}, data={self.data.hex()}, received_crc={self.received_crc.hex()}, CRC_ok={self.CRC_ok})"

    def calculate_crc(self):
        # Метод для расчета CRC16 Modbus для текущего кадра (адрес + функция + данные)
        message = bytes([self.address, self.function]) + self.data
        crc = 0xFFFF  # Начальное значение для CRC16

        for byte in message:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:  # Проверяем младший бит
                    crc >>= 1
                    crc ^= 0xA001  # Полином для Modbus
                else:
                    crc >>= 1
        
        # Возвращаем CRC в виде двух байтов
        return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    def check_crc(self):
        # Метод для проверки корректности CRC
        calculated_crc = self.calculate_crc()  # Рассчитываем CRC
        # Если полученный CRC совпадает с вычисленным, то CRC корректен
        self.CRC_ok = self.received_crc == calculated_crc

    def get_error_description(self, error_code):
        """Возвращает текстовое описание кода ошибки Modbus"""
        error_descriptions = {
            0x01: "Недопустимая функция",
            0x02: "Недопустимый адрес данных",
            0x03: "Недопустимое значение данных",
            0x04: "Ошибка устройства",
            0x05: "Подтверждение",
            0x06: "Устройство занято",
            0x07: "Отрицательное подтверждение",
            0x08: "Ошибка четности памяти",
            0x0A: "Шлюз недоступен",
            0x0B: "Целевое устройство шлюза не ответило",
        }
        return error_descriptions.get(error_code, f"Неизвестная ошибка (0x{error_code:02X})")

    def get_list(self):
        # Проверяем, является ли это исключением (MSB функции = 1)
        is_exception = (self.function & 0x80) != 0
        base_function = self.function & 0x7F  # базовая функция без флага исключения

        # Определяем тип функции
        is_read_function = base_function in (0x01, 0x02, 0x03, 0x04)
        is_write_single = base_function in (0x05, 0x06)  # Write Single Coil/Register
        is_write_multiple = base_function in (0x0F, 0x10)  # Write Multiple Coils/Registers (15, 16)

        # Определяем, является ли кадр ответом
        is_response = False
        # Если это исключение, это всегда ответ
        if is_exception:
            is_response = True
        elif is_read_function and len(self.data) >= 1:
            # Для функций чтения: в ответе первый байт после функции — количество байт данных
            byte_count = self.data[0]
            is_response = (len(self.data) == 1 + byte_count)
        elif is_write_single:
            # Для функций 5 и 6: если длина данных = 4 байта, это может быть и запрос и ответ
            # По умолчанию считаем запросом (ответ будет обработан отдельно при сопоставлении)
            # Ответ для функций 5 и 6 - это эхо запроса (4 байта)
            is_response = False  # Будет определяться в main.py при сопоставлении с запросом
        elif is_write_multiple:
            # Для функций 15 и 16:
            # Запрос: длина данных > 5 байт (адрес 2 + количество 2 + байт количества 1 + значения)
            # Ответ: длина данных = 4 байта (адрес 2 + количество 2)
            if len(self.data) == 4:
                is_response = True
            else:
                is_response = False

        message_type = "Ответ" if is_response else "Запрос"

        # Инициализация переменных для колонок
        first_register_addr_value = None
        registers_count_display = "-"
        byte_count_display = "-"  # Новый столбец "Количество байт далее"
        payload = b""

        # Обработка исключений
        if is_exception and len(self.data) >= 1:
            error_code = self.data[0]
            error_desc = self.get_error_description(error_code)
            registers_count_display = f"Ошибка: {error_desc}"
            payload = self.data[1:] if len(self.data) > 1 else b""
        
        # Обработка функций чтения (0x01-0x04)
        elif is_read_function:
            if is_response and len(self.data) >= 1:
                # Ответ: первый байт - количество байт данных
                registers_count_value = int(self.data[0])
                registers_count_display = registers_count_value
                payload = self.data[1:]
            elif not is_response and len(self.data) >= 4:
                # Запрос: адрес регистра (2 байта) + количество (2 байта)
                first_register_addr_value = int.from_bytes(self.data[0:2], byteorder="big")
                registers_count_value = int.from_bytes(self.data[2:4], byteorder="big")
                registers_count_display = registers_count_value
                payload = self.data[4:] if len(self.data) > 4 else b""
        
        # Обработка функций записи одной единицы (5, 6)
        elif is_write_single:
            if len(self.data) >= 4:
                # И запрос, и ответ имеют одинаковую структуру: адрес (2) + значение (2)
                first_register_addr_value = int.from_bytes(self.data[0:2], byteorder="big")
                if is_response:
                    # Для ответа: количество регистров = 1 (эхо запроса)
                    registers_count_display = 1
                    # Данные ответа - это значение (2 байта)
                    payload = self.data[2:4]
                else:
                    # Для запроса: количество регистров = 1
                    registers_count_display = 1
                    # Данные запроса - это значение (2 байта)
                    payload = self.data[2:4]
        
        # Обработка функций записи множественных единиц (15, 16)
        elif is_write_multiple:
            if is_response and len(self.data) >= 4:
                # Ответ: адрес (2) + количество (2)
                first_register_addr_value = int.from_bytes(self.data[0:2], byteorder="big")
                registers_count_value = int.from_bytes(self.data[2:4], byteorder="big")
                registers_count_display = registers_count_value
                payload = b""  # В ответе нет данных значений
            elif not is_response and len(self.data) >= 5:
                # Запрос: адрес (2) + количество (2) + байт количества (1) + значения
                first_register_addr_value = int.from_bytes(self.data[0:2], byteorder="big")
                registers_count_value = int.from_bytes(self.data[2:4], byteorder="big")
                registers_count_display = registers_count_value
                byte_count_display = int(self.data[4])  # Байт количества данных
                payload = self.data[5:]  # Данные значений

        # Формируем итоговые значения для отображения
        first_register_addr_display = first_register_addr_value if first_register_addr_value is not None else "-"
        data_display = payload if payload else "-"

        return [
            0,                                # Счетчик сообщений (заполнится в UI)
            0,                                # Время (заполнится в UI)
            message_type,                     # Тип сообщения
            self.address,                     # Адрес
            self.function,                    # Функция
            first_register_addr_display,     # Адрес 1-го регистра
            registers_count_display,          # Кол-во регистров/байт
            byte_count_display,               # Количество байт далее (только для 15, 16)
            data_display,                     # Данные
            self.received_crc,                # CRC
            self.CRC_ok                       # CRC_OK
        ]

