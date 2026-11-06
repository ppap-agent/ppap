import {createContext} from 'react'
import {nativeFactAnswersAdapter} from './fact-answers-contract.ts'

// Production readers use HTTP unless an explicit preview boundary supplies an adapter.
export const FactAnswersContext = createContext(nativeFactAnswersAdapter)
