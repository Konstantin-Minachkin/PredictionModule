
import threading
import time


class Thread(object):
    """ Threading example class
    The run() method will be started and it will run in the background
    until the application exits.
    """

    def __init__(self, interval=1):
        """ Constructor
        :type interval: int
        :param interval: Check interval, in seconds
        """
        self.interval = interval
        thread = threading.Thread(target=self.run, args=())
        #thread.daemon = True                            # Daemonize thread
        thread.start()                                  # Start the execution

    def run(self):
        """ Method that runs forever """
        while True:
            print('UpdateRoote')
            time.sleep(self.interval)

if __name__ == '__main__':
    backgound = Thread(interval=20)
    while True:
        print('Hello')
        time.sleep(10)