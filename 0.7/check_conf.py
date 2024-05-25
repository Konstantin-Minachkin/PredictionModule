# -*- coding: utf-8 -*-

from config import Config

if __name__ == '__main__':
    print('Check for errors:\n', Config('myK/conf_old.yaml').conf_review())