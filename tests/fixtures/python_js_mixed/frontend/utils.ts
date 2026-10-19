export type UserId = string;
export interface User {
  id: UserId;
}

export const formatUser = (user: User): string => user.id;
