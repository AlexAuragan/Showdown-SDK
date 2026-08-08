class ParserException(Exception):
    """General Parser excepitons"""
    pass

class WrongRoomException(ParserException):
    """When the parser got a message meant for another room"""
    def __init__(self, current_room_id: str, given_room_id: str, *args: object) -> None:
        super().__init__(*args)
        self.current_room_id = current_room_id
        self.given_room_id = given_room_id

    def __str__(self):
        return f"Parser got a message meant for another room, parser's room: {self.current_room_id}, given room: {self.given_room_id}"


class InvalidActionError(Exception):
    def __init__(self, category: str, message: str, *args: object) -> None:
        super().__init__(*args)
        self.category = category
        self.message = message

    def __str__(self) -> str:
        return f"Last action was invalid: [{self.category}] {self.message}"

class ObsoleteRequestIdError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)

    def __str__(self) -> str:
        return super().__str__()
