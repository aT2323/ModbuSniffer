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

        # Определяем, является ли кадр ответом для функций чтения (0x01,0x02,0x03,0x04)
        is_read_function = base_function in (0x01, 0x02, 0x03, 0x04)
        is_read_response = False
        # Если это исключение, это всегда ответ
        if is_exception:
            is_read_response = True
        elif len(self.data) >= 1 and is_read_function:
            # В ответе первый байт после функции — количество байт данных
            byte_count = self.data[0]
            is_read_response = (len(self.data) == 1 + byte_count)

        message_type = "Ответ" if is_read_response else "Запрос"

        # Формируем колонки
        # Адрес первого регистра (для запроса: 2 байта после функции; для ответа: '-')
        if not is_read_response and len(self.data) >= 2 and not is_exception:
            first_register_addr_value = int.from_bytes(self.data[0:2], byteorder="big")
        else:
            first_register_addr_value = None

        # Количество регистров:
        # - для запроса: 2 байта после адреса первого регистра (data[2:4])
        # - для ответа: 1 байт (количество байт данных) = data[0]
        # - для исключения: первый байт данных - код ошибки
        if is_exception and len(self.data) >= 1:
            error_code = self.data[0]
            error_desc = self.get_error_description(error_code)
            registers_count_display = f"Ошибка: {error_desc}"
        elif is_read_response and len(self.data) >= 1:
            registers_count_value = int(self.data[0])
            registers_count_display = registers_count_value
        elif not is_read_response and len(self.data) >= 4:
            registers_count_value = int.from_bytes(self.data[2:4], byteorder="big")
            registers_count_display = registers_count_value
        else:
            registers_count_display = "-"

        # Данные:
        # - для запроса: остаток после первых 4 байт (если есть)
        # - для ответа: данные после байта количества (если есть)
        # - для исключения: остальные данные (обычно пусто, код ошибки уже в регистрах)
        if is_exception:
            payload = self.data[1:] if len(self.data) > 1 else b""
        elif is_read_response:
            payload = self.data[1:]
        else:
            payload = self.data[4:] if len(self.data) > 4 else b""

        # Для ответов и исключений поле "Адрес первого регистра" — прочерк
        first_register_addr_display = first_register_addr_value if first_register_addr_value is not None else "-"
        data_display = payload if payload else "-"

        return [
            0,                                # Счетчик сообщений (заполнится в UI)
            0,                                # Время (заполнится в UI)
            message_type,                     # Тип сообщения
            self.address,                     # Адрес
            self.function,                    # Функция
            first_register_addr_display if not is_read_response else "-",
            registers_count_display,          # Кол-во регистров/байт
            data_display,                     # Данные
            self.received_crc,                # CRC
            self.CRC_ok                       # CRC_OK
        ]

