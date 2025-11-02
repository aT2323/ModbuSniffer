class Frame:
    def __init__(self, message: bytes):

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

    def get_list(self):
        return([0,0,self.address,self.function, self.data, self.received_crc, self.CRC_ok])

