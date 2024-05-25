
from ..MigrationBase import migration

class Migration1():
    
    # если используем transation - то функции apply надо передавать не connection_obj, а connection_dictionary
    # возваращает false - если что-то пошло не так
    @migration
    # connection_obj is a connectionObject returned from psycopg2 connect()
    def apply(self, cursor, **kwargs):
        print(f"Пустая миграция (тестовое сообщ)")

            