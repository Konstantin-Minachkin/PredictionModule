# -*- coding: utf-8 -*-
from ryu.ofproto import ofproto_v1_3_parser as parser
from ryu.ofproto import ofproto_v1_3 as proto


class Tables:

    def __init__(self, *args, **kwargs):
        super(Tables).__init__(*args, **kwargs)
        self.tables = []

    def __str__(self):
        i = []
        for t in self.tables:
            i.append(str(t))
        return '\n'.join(i)

    def get_table(self, name):
        i = -1
        for table in self.tables:
            i+=1
            if table.name == name:
                return table, i
        return Table(''), -1
    
    def table_id(self, table_name):
        table, id = self.get_table(table_name)
        return id

    def next_table_id(self, table_name):
        #узнает id следующей таблицы для указанной таблицы
        id = self.table_id(table_name)
        if id != -1 and id+1 <= len(self.tables)-1:
            return id+1
        else:
            return -1
    
    def add_table(self, table, past_table_name = None):
        assert (isinstance(table, Table), 'have to add Table type')
        if self.table_id(table.name) != -1:
            print('Also has table with the same name')
            return 
        
        if past_table_name:
            id = self.table_id(past_table_name)
            if id == -1:
                print('No table with past_table_name found')
                return
            self.tables.insert(id+1, table)
        else:
            #add as the first table
            self.tables.insert(0, table)
        
        #send event to clear all tables
        # ev = events.EventClearAllFlows(past_table_name or table.name)
        # self.send_event_to_observers(ev)
       
    def goto_next_of(self, table):
        """Add goto next table instruction."""
        if table.next_table_name is None:
            return [Table.goto_table(self.next_table_id(table.name))]
        else:
            return [Table.goto_table(self.table_id(table.next_table_name))]
    
    def goto_next_by_name(self, table_name):
        """Add goto next table instruction."""
        return [Table.goto_table(self.next_table_id(table_name))]

    
    def goto_this(self, table_id):
        """Add goto next table instruction."""
        return [Table.goto_table(table_id)]


class Table:
    """Wrapper for an OpenFlow table."""

    def __init__(self, name, next_table_name=None):
        self.name = name
        self.next_table_name = next_table_name

    def change_next(self, next_table_name):
        self.next_table_name = next_table_name

    @staticmethod
    def goto_table(table_id):
        "Generate an OFPInstructionGotoTable message"
        return parser.OFPInstructionGotoTable(table_id)
    
    def __str__(self):
        return '<name=%s, next_table_name = %s>' % (self.name, self.next_table_name)
