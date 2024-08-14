class Event:
    def __init__(self):
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)

    def unsubscribe(self, listener):
        self.listeners.remove(listener)

    def notify(self, *args, **kwargs):
        for listener in self.listeners:
            listener(*args, **kwargs)
