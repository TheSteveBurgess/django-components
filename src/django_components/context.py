"""
This file centralizes various ways we use Django's Context class
pass data across components, nodes, slots, and contexts.

You can think of the Context as our storage system.
"""

from typing import TYPE_CHECKING, Optional

from django.template import Context

from django_components.app_settings import SlotContextBehavior, app_settings
from django_components.logger import trace_msg
from django_components.utils import find_last_index

if TYPE_CHECKING:
    from django_components.slots import FillContent


_FILLED_SLOTS_CONTENT_CONTEXT_KEY = "_DJANGO_COMPONENTS_FILLED_SLOTS"
_OUTER_ROOT_CTX_CONTEXT_KEY = "_DJANGO_COMPONENTS_OUTER_ROOT_CTX"
_SLOT_COMPONENT_ASSOC_KEY = "_DJANGO_COMPONENTS_SLOT_COMP_ASSOC"
_PARENT_COMP_KEY = "_DJANGO_COMPONENTS_PARENT_COMP"
_CURRENT_COMP_KEY = "_DJANGO_COMPONENTS_CURRENT_COMP"


def prepare_context(
    context: Context,
    outer_context: Optional[Context],
    component_id: str,
) -> None:
    """Initialize the internal context state."""
    # This is supposed to run ALWAYS at `Component.render()`
    if outer_context is not None:
        set_outer_root_context(context, outer_context)

    # Initialize mapping dicts within this rendering run.
    # This is shared across the whole render chain, thus we set it only once.
    if _SLOT_COMPONENT_ASSOC_KEY not in context:
        context[_SLOT_COMPONENT_ASSOC_KEY] = {}
    if _FILLED_SLOTS_CONTENT_CONTEXT_KEY not in context:
        context[_FILLED_SLOTS_CONTENT_CONTEXT_KEY] = {}

    # If we're inside a forloop, we need to make a disposable copy of slot -> comp
    # mapping, which can be modified in the loop. We do so by copying it onto the latest
    # context layer.
    #
    # This is necessary, because otherwise if we have a nested loop with a same
    # component used recursively, the inner slot -> comp mapping would leak into the outer.
    #
    # NOTE: If you ever need to debug this, insert a print/debug statement into
    # `django.template.defaulttags.ForNode.render` to inspect the context object
    # inside the for loop.
    if "forloop" in context:
        context.dicts[-1][_SLOT_COMPONENT_ASSOC_KEY] = context[_SLOT_COMPONENT_ASSOC_KEY].copy()

    set_component_id(context, component_id)


def make_isolated_context_copy(context: Context) -> Context:
    # Even if contexts are isolated, we still need to pass down the
    # metadata so variables in slots can be rendered using the correct context.
    root_ctx = get_outer_root_context(context)
    slot_assoc = context.get(_SLOT_COMPONENT_ASSOC_KEY, {})
    slot_fills = context.get(_FILLED_SLOTS_CONTENT_CONTEXT_KEY, {})

    context_copy = context.new()
    context_copy[_SLOT_COMPONENT_ASSOC_KEY] = slot_assoc
    context_copy[_FILLED_SLOTS_CONTENT_CONTEXT_KEY] = slot_fills
    set_outer_root_context(context_copy, root_ctx)
    copy_forloop_context(context, context_copy)

    context_copy[_CURRENT_COMP_KEY] = context.get(_CURRENT_COMP_KEY, None)
    context_copy[_PARENT_COMP_KEY] = context.get(_PARENT_COMP_KEY, None)

    return context_copy


def set_component_id(context: Context, component_id: str) -> None:
    """
    We use the Context object to pass down info on inside of which component
    we are currently rendering.
    """
    # Store the previous component so we can detect if the current component
    # is the top-most or not. If it is, then "_parent_component_id" is None
    context[_PARENT_COMP_KEY] = context.get(_CURRENT_COMP_KEY, None)
    context[_CURRENT_COMP_KEY] = component_id


def get_slot_fill(context: Context, component_id: str, slot_name: str) -> Optional["FillContent"]:
    """
    Use this function to obtain a slot fill from the current context.

    See `set_slot_fill` for more details.
    """
    trace_msg("GET", "FILL", slot_name, component_id)
    slot_key = f"{component_id}__{slot_name}"
    return context[_FILLED_SLOTS_CONTENT_CONTEXT_KEY].get(slot_key, None)


def set_slot_fill(context: Context, component_id: str, slot_name: str, value: "FillContent") -> None:
    """
    Use this function to set a slot fill for the current context.

    Note that we make use of the fact that Django's Context is a stack - we can push and pop
    extra contexts on top others.
    """
    trace_msg("SET", "FILL", slot_name, component_id)
    slot_key = f"{component_id}__{slot_name}"
    context[_FILLED_SLOTS_CONTENT_CONTEXT_KEY][slot_key] = value


def get_outer_root_context(context: Context) -> Optional[Context]:
    """
    Use this function to get the outer root context.

    See `set_outer_root_context` for more details.
    """
    return context.get(_OUTER_ROOT_CTX_CONTEXT_KEY)


def set_outer_root_context(context: Context, outer_ctx: Optional[Context]) -> None:
    """
    Use this function to set the outer root context.

    When we consider a component's template, then outer context is the context
    that was available just outside of the component's template (AKA it was in
    the PARENT template).

    Once we have the outer context, next we get the outer ROOT context. This is
    the context that was available at the top level of the PARENT template.

    We pass through this context to allow to configure how slot fills should be
    rendered using the `SLOT_CONTEXT_BEHAVIOR` setting.
    """
    # Special case for handling outer context of top-level components when
    # slots are isolated. In such case, the entire outer context is to be the
    # outer root ctx.
    if (
        outer_ctx
        and not context.get(_PARENT_COMP_KEY)
        and app_settings.SLOT_CONTEXT_BEHAVIOR == SlotContextBehavior.ISOLATED
        and _OUTER_ROOT_CTX_CONTEXT_KEY in context  # <-- Added to avoid breaking tests
    ):
        outer_root_context = outer_ctx.new()
        outer_root_context.push(outer_ctx.flatten())

    # In nested components, the context generated from `get_context_data`
    # is found at index 1.
    # NOTE:
    # - Index 0 are the defaults set in BaseContext
    # - Index 1 is the context generated by `Component.get_context_data`
    #   of the parent's component
    # - All later indices (2, 3, ...) are extra layers added by the rendering
    #   logic (each Node usually adds it's own context layer)
    elif outer_ctx and len(outer_ctx.dicts) > 1:
        outer_root_context = outer_ctx.new()
        outer_root_context.push(outer_ctx.dicts[1])

    # Fallback
    else:
        outer_root_context = Context()

    # Include the mappings.
    if _SLOT_COMPONENT_ASSOC_KEY in context:
        outer_root_context[_SLOT_COMPONENT_ASSOC_KEY] = context[_SLOT_COMPONENT_ASSOC_KEY]
    if _FILLED_SLOTS_CONTENT_CONTEXT_KEY in context:
        outer_root_context[_FILLED_SLOTS_CONTENT_CONTEXT_KEY] = context[_FILLED_SLOTS_CONTENT_CONTEXT_KEY]

    context[_OUTER_ROOT_CTX_CONTEXT_KEY] = outer_root_context


def set_slot_component_association(
    context: Context,
    slot_id: str,
    component_id: str,
) -> None:
    """
    Set association between a Slot and a Component in the current context.

    We use SlotNodes to render slot fills. SlotNodes are created only at Template
    parse time.
    However, when we refer to components with slots in (another) template (using
    `{% component %}`), we can render the same component multiple time. So we can
    have multiple FillNodes intended to be used with the same SlotNode.

    So how do we tell the SlotNode which FillNode to render? We do so by tagging
    the ComponentNode and FillNodes with a unique component_id, which ties them
    together. And then we tell SlotNode which component_id to use to be able to
    find the correct Component/Fill.

    We don't want to store this info on the Nodes themselves, as we need to treat
    them as immutable due to caching of Templates by Django.

    Hence, we use the Context to store the associations of SlotNode <-> Component
    for the current context stack.
    """
    # Store associations on the latest context layer so that we can nest components
    # onto themselves (component A is rendered in slot fill of component A).
    # Otherwise, they would overwrite each other as the ComponentNode and SlotNodes
    # are re-used, so their IDs don't change across these two occurences.
    latest_dict = context.dicts[-1]
    if _SLOT_COMPONENT_ASSOC_KEY not in latest_dict:
        latest_dict[_SLOT_COMPONENT_ASSOC_KEY] = context[_SLOT_COMPONENT_ASSOC_KEY].copy()
    context[_SLOT_COMPONENT_ASSOC_KEY][slot_id] = component_id


def get_slot_component_association(context: Context, slot_id: str) -> str:
    """
    Given a slot ID, get the component ID that this slot is associated with
    in this context.

    See `set_slot_component_association` for more details.
    """
    return context[_SLOT_COMPONENT_ASSOC_KEY][slot_id]


def copy_forloop_context(from_context: Context, to_context: Context) -> None:
    """Forward the info about the current loop"""
    # Note that the ForNode (which implements for loop behavior) does not
    # only add the `forloop` key, but also keys corresponding to the loop elements
    # So if the loop syntax is `{% for my_val in my_lists %}`, then ForNode also
    # sets a `my_val` key.
    # For this reason, instead of copying individual keys, we copy the whole stack layer
    # set by ForNode.
    if "forloop" in from_context:
        forloop_dict_index = find_last_index(from_context.dicts, lambda d: "forloop" in d)
        to_context.update(from_context.dicts[forloop_dict_index])
