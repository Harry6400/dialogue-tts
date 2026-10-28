import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {WorkspacePagination} from '../app/WorkspacePagination';

it('navigates directly to both ends of a six-hundred-page batch with bounded controls',()=>{
  const onPage=vi.fn();
  const view=render(<WorkspacePagination page={1} total={60000} loading={false} onPage={onPage}/>);
  expect(screen.getByText('1–100 / 60,000 通')).toBeVisible();
  expect(screen.getAllByRole('button')).toHaveLength(4);
  expect(screen.getByRole('button',{name:'第一页'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'最后一页'}));expect(onPage).toHaveBeenLastCalledWith(600);
  view.rerender(<WorkspacePagination page={600} total={60000} loading={false} onPage={onPage}/>);
  expect(screen.getByText('59,901–60,000 / 60,000 通')).toBeVisible();
  expect(screen.getByRole('button',{name:'最后一页'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'第一页'}));expect(onPage).toHaveBeenLastCalledWith(1);
});

it('rejects invalid page jumps and lets Escape restore the current page without leaving review',()=>{
  const onPage=vi.fn();render(<WorkspacePagination page={2} total={205} loading={false} onPage={onPage}/>);
  const input=screen.getByRole('textbox',{name:'跳转页码'});
  for(const value of ['0','4','1.5','abc','']){
    fireEvent.change(input,{target:{value}});fireEvent.keyDown(input,{key:'Enter'});
    expect(input).toHaveAttribute('aria-invalid','true');
    expect(screen.getByRole('alert')).toHaveTextContent('请输入 1–3 的页码');
  }
  expect(onPage).not.toHaveBeenCalled();
  fireEvent.keyDown(input,{key:'Escape'});expect(input).toHaveValue('2');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  fireEvent.change(input,{target:{value:'3'}});fireEvent.keyDown(input,{key:'Enter'});
  expect(onPage).toHaveBeenCalledExactlyOnceWith(3);
});
