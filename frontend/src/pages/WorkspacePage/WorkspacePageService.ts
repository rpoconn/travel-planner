// Pure logic for WorkspacePage: when the trips panel (the left sidebar) is open.
//
// The panel stays open while the user browses trips. Once they start working in a trip (a click
// or key press in its problem list, itinerary or chat) it hides to give the trip the room. The
// header's toggle always wins: a panel reopened by hand stays open until the user hides it or
// picks another trip.

// ----- types -----

export interface TripsPanelState {
    open: boolean
    /** Hide the panel on the next interaction with the trip. */
    collapseOnInteract: boolean
}

export type TripsPanelEvent =
    /** A trip was picked or created in the panel. */
    | 'trip_selected'
    /** A click or key press inside the selected trip. */
    | 'trip_interaction'
    /** The header's show/hide button. */
    | 'toggled'

// ----- state -----

/** The panel on page load: open, and hiding once the user works in the trip the URL names. */
export function initialTripsPanel(hasPlan: boolean): TripsPanelState {
    return { open: true, collapseOnInteract: hasPlan }
}

export function tripsPanelReducer(state: TripsPanelState, event: TripsPanelEvent): TripsPanelState {
    switch (event) {
        case 'trip_selected':
            return { open: state.open, collapseOnInteract: true }
        case 'trip_interaction':
            // Returning the same object lets React skip the render on every later click.
            return state.collapseOnInteract ? { open: false, collapseOnInteract: false } : state
        case 'toggled':
            return { open: !state.open, collapseOnInteract: false }
    }
}

/** The toggle button's label: what pressing it does. */
export function tripsToggleLabel(open: boolean): string {
    return open ? 'Hide trips' : 'Show trips'
}
