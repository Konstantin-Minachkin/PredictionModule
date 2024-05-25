import psycopg2

# class MigrationBase():


# def transaction(params):
    # def actual_decorator():
def migration(func):
    def wrapper(*args, **kwargs):
        try:
            # подключиться к бд
            # начать миграцию внутри транзакции
            pg_connection_dict = kwargs.get("pg_con")
            with psycopg2.connect(**pg_connection_dict) as conn:
                print(f'Exectuing migration: {args[0].__class__.__name__}')
                with conn.cursor() as cur:
                    func(args[0], cursor=cur, **kwargs) #args[0] == self
                # commit the transaction
                    # вписать в табличку имя миграции
                    cur.execute(f"INSERT INTO _migrations(name) VALUES ('{type(args[0]).__name__}');")
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            print(f'Error while exectuing migration: {args[0].__class__.__name__}:\n {error}')
            if 'conn' in locals():
                conn.rollback()
            return False
        return True
    
    return wrapper
    # return actual_decorator


def select(conn_dict, select_request):
    # запрос вовзращает данные из select запроса в виде словаря
    try:
        with psycopg2.connect(**conn_dict) as conn:
            with conn.cursor() as cur:
                cur.execute(select_request)
                rows = cur.fetchall()
                return rows
    except (Exception, psycopg2.DatabaseError) as error:
        print(f'Error while exectuing request: {select_request}:\n {error}')