import { mockCases } from "../mocks/cases";
import type { DisputeCase } from "../types/dispute";

export const caseService = {
  list(): Promise<DisputeCase[]> { return Promise.resolve(mockCases); },
  getById(id: string): Promise<DisputeCase | undefined> { return Promise.resolve(mockCases.find((item) => item.id === id)); }
};
