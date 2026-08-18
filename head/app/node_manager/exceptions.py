class NodeChannelError(Exception):
    """Base error for anything that goes wrong talking to a node."""


class NodeUnreachableError(NodeChannelError):
    """Neither the direct path nor the Reality-tunnelled fallback could reach the node."""


class NodeNotProvisionedError(NodeChannelError):
    """The node has no dedicated control-channel inbound, so no fallback path exists for it."""
