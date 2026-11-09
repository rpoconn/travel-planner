import { describe, expect, it } from 'vitest'

import {
    initialTripsPanel,
    tripsPanelReducer,
    tripsToggleLabel,
    type TripsPanelEvent,
    type TripsPanelState,
} from './WorkspacePageService'

// ----- setup -----

function after(state: TripsPanelState, ...events: TripsPanelEvent[]): TripsPanelState {
    return events.reduce(tripsPanelReducer, state)
}

// ----- tests -----

describe('initialTripsPanel', () => {
    it('starts open, and hides on interaction only when a trip is selected', () => {
        expect(initialTripsPanel(false)).toEqual({ open: true, collapseOnInteract: false })
        expect(initialTripsPanel(true)).toEqual({ open: true, collapseOnInteract: true })
    })
})

describe('tripsPanelReducer', () => {
    it('stays open while trips are picked one after another', () => {
        const state = after(initialTripsPanel(false), 'trip_selected', 'trip_selected')

        expect(state.open).toBe(true)
    })

    it('hides on the first interaction with the selected trip', () => {
        const state = after(initialTripsPanel(false), 'trip_selected', 'trip_interaction')

        expect(state).toEqual({ open: false, collapseOnInteract: false })
    })

    it('ignores interactions when no trip was selected', () => {
        const state = initialTripsPanel(false)

        expect(tripsPanelReducer(state, 'trip_interaction')).toBe(state)
    })

    it('stays open after being reopened by hand', () => {
        const state = after(initialTripsPanel(true), 'trip_interaction', 'toggled')

        expect(state.open).toBe(true)
        expect(tripsPanelReducer(state, 'trip_interaction')).toBe(state)
    })

    it('hides again once another trip is picked and worked in', () => {
        const state = after(
            initialTripsPanel(true),
            'trip_interaction',
            'toggled',
            'trip_selected',
            'trip_interaction',
        )

        expect(state.open).toBe(false)
    })

    it('hides by hand, and stays hidden through interactions', () => {
        const state = after(initialTripsPanel(false), 'toggled', 'trip_interaction')

        expect(state.open).toBe(false)
    })
})

describe('tripsToggleLabel', () => {
    it('says what the button does', () => {
        expect(tripsToggleLabel(true)).toBe('Hide trips')
        expect(tripsToggleLabel(false)).toBe('Show trips')
    })
})
