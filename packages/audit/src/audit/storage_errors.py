"""Storage outcomes shared by product state and the execution loop."""


class ObjectExists(Exception):
    pass


class ObjectMissing(Exception):
    pass


class ObjectTooLarge(Exception):
    pass
