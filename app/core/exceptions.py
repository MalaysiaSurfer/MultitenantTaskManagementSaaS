class NotFoundException(Exception):
    pass


class EmailAlreadyRegistered(Exception):
    pass


class ProjectAlreadyExists(Exception):
    pass


class AlreadyMember(Exception):
    pass


class LastOwnerError(Exception):
    pass


class InsufficientPermissions(Exception):
    pass
